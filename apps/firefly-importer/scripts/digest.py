import json, os, re, ssl, smtplib, urllib.request, datetime
from email.message import EmailMessage

BASE  = os.environ["FIREFLY_III_URL"].rstrip("/")
TOKEN = os.environ["FIREFLY_III_ACCESS_TOKEN"]
STALE = int(os.environ.get("DIGEST_STALE_DAYS", "3"))      # absolute floor
CAP   = int(os.environ.get("DIGEST_STALE_MAX_DAYS", "60"))  # absolute ceiling
MARGIN= float(os.environ.get("DIGEST_STALE_MARGIN", "1.25")) # slack on the record
RUN_MAX_AGE = int(os.environ.get("DIGEST_RUN_MAX_AGE_HOURS", "12"))
CONSENT_DAYS = int(os.environ.get("DIGEST_CONSENT_DAYS", "90"))       # PSD2 RTS maximum
CONSENT_WARN = int(os.environ.get("DIGEST_CONSENT_WARN_DAYS", "7"))   # notice wanted  # newest import pod must be younger
IMPORTER_URL = os.environ.get("DIGEST_IMPORTER_URL", "").rstrip("/")  # omitted if unset
CONSENT_SYNCED = os.environ.get("DIGEST_CONSENT_SYNCED", "false").lower() == "true"
# Accounts that legitimately go quiet (manually maintained, dormant). Without
# this they would trip the staleness check every single night, and a warning
# that fires every night is one nobody reads.
IGNORE = set(json.loads(os.environ.get("DIGEST_IGNORE_STALE", "[]")))
ALWAYS = os.environ.get("DIGEST_ALWAYS_SEND", "false").lower() == "true"
WINDOW= int(os.environ.get("DIGEST_WINDOW_HOURS", "24"))

def api(path):
    req = urllib.request.Request(
        BASE + path,
        headers={"Authorization": "Bearer " + TOKEN, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)

def heartbeat():
    """Tell the dead man's switch this run finished.

    A healthy night sends no mail, so an absent mail is not evidence of
    anything: a suspended CronJob, a crashed job and an unreachable mailbox
    all look exactly like "nothing to report". The far end alerts when this
    ping stops arriving, which is the only way that difference becomes
    visible. Called on the paths that got all the way through and nowhere
    else - a run that dies before the mail is out must NOT keep it green.
    """
    url = os.environ.get("DIGEST_HEARTBEAT_URL", "")
    if not url:
        return
    try:
        urllib.request.urlopen(url, timeout=10).read()
    except Exception as e:   # a missed ping is not worth failing the job over
        print(f"heartbeat ping failed: {e}")

today = datetime.date.today()
since = today - datetime.timedelta(days=max(1, WINDOW // 24))

# New transactions in the window, counted per asset account.
fresh = {}
page = 1
while True:
    d = api(f"/api/v1/transactions?start={since}&end={today}&limit=500&page={page}")
    for t in d["data"]:
        for s in t["attributes"]["transactions"]:
            for side in ("source", "destination"):
                if s.get(f"{side}_type") == "Asset account":
                    fresh[s[f"{side}_name"]] = fresh.get(s[f"{side}_name"], 0) + 1
    if page >= max(1, d["meta"]["pagination"]["total_pages"]):
        break
    page += 1

# A fixed "silent for N days" rule is useless here: the giro accounts see
# traffic daily, the rental accounts once a month, SpaceX never. So each
# account is judged against its OWN history: the longest stretch it has
# already been quiet without anything being wrong. Only a silence longer
# than that is evidence of a problem.
#
# The average gap is the wrong statistic for this. Rent accounts book several
# times on one day and then nothing for a month, so their median gap is a few
# days while a perfectly healthy quiet stretch is four weeks - a multiple of
# the median flags them every month for being normal.
def quiet_record(aid):
    d = api(f"/api/v1/accounts/{aid}/transactions?limit=60")
    ds = sorted({s["date"][:10] for t in d["data"] for s in t["attributes"]["transactions"]})
    if len(ds) < 6:
        return None                       # too little history to judge
    dts  = [datetime.date.fromisoformat(x) for x in ds]
    return max((dts[i+1] - dts[i]).days for i in range(len(dts)-1))

rows, stale = [], []
# id -> (name, iban), for the re-authorisation warning: a consent is restored by hand
# and the only thing that identifies a bank account on that form is its IBAN.
asset_by_id = {}
for a in api("/api/v1/accounts?type=asset&limit=200")["data"]:
    at = a["attributes"]
    asset_by_id[a["id"]] = (at["name"], (at.get("iban") or "").replace(" ", ""))
    if not at.get("active"):
        continue
    last = (at.get("last_activity") or "")[:10]
    age  = (today - datetime.date.fromisoformat(last)).days if last else None
    n    = fresh.get(at["name"], 0)
    rows.append((at["name"], n, last or "-", age))
    if age is None or at["name"] in IGNORE:
        continue
    rec = quiet_record(a["id"])
    if rec is None:
        continue
    # The margin matters for anything on a monthly cycle: its record gap IS
    # one cycle, so a rent payment landing two days late would trip the alarm
    # every time it happens. The cap matters in the other direction - for an
    # account that was dormant once, a single six-month gap in its history
    # would otherwise excuse any future silence.
    limit = max(STALE, min(int(rec * MARGIN) + 1, CAP))
    if age > limit:
        stale.append((at["name"], last, age, rec, limit))

rows.sort(key=lambda r: (-r[1], r[0]))
# Deliberately no balances: this mail answers "did the import run?", and
# account balances do not belong in a daily plaintext notification.
w = max([len(r[0]) for r in rows] + [7])
lines = [f"{'Account'.ljust(w)}  {'new':>4}  {'last activity':>13}",
         "-" * (w + 22)]
lines += [f"{r[0].ljust(w)}  {r[1]:>4}  {r[2]:>13}" for r in rows]

# --- what the importer itself did last run -------------------------------
# The digest can only see what arrived. A run that aborts (expired consent) or
# a line rejected for a real reason leaves no trace in the data, so read the
# log of tonight's auto-import pod too and keep only what is NOT routine noise
# (see below). That pod runs the importer CLI, so its log holds exactly one
# run: the CLI's console progress lines plus the importer's own log lines at
# LOG_LEVEL (info), which still include every ERROR.
#
# Lines the importer logs at ERROR level that do not mean anything failed:
#   a115/a117 - transaction already exists, by external_id and by content. Every
#               night, because the bank re-offers a rolling window.
#   "Exit code" - logged through the error channel for EVERY outcome, SUCCESS
#               included. A genuinely fatal exit always writes a readable error
#               line of its own right before it, so nothing is lost by dropping
#               these; the one exception, an empty /import directory, prints
#               a console line that CONSOLE_ERROR below picks up.
#   "tag"     - the import tag is created per run and reused for every following
#               transaction, so Firefly III rejects the create as a duplicate
#               name. The tag is attached to the transaction regardless.
NOT_A_FAILURE = ("[a115]", "[a117]", "Exit code is ", '{"tag":[')
#
# An empty download is the other routine non-event, and needs more than a
# text match. A bank that simply had no new booking makes the importer log
# "Zero transactions!" AND abort the config with "Too many errors in the data
# conversion (1)", so one quiet account produces two alarming lines every
# night. Both are dropped, but only for the config that was empty: a second
# config failing for a real reason still shows.
ZERO_DOWNLOAD = re.compile(r"Zero transactions!|\[eb003\]|\[a111\]")
# The importer aborts a config as soon as ONE conversion error exists (the
# "too many" wording is misleading), and the number in the message is that
# error count. "(1)" next to an empty download therefore means the empty
# download was the only thing wrong; a higher count means something else
# failed as well, so the message is kept.
TOO_MANY      = re.compile(r"Too many errors in the data conversion \((\d+)\)")
# A bank consent under PSD2 lasts ~90 days and then simply stops working. Enable
# Banking answers every call with 401 CLOSED_SESSION, which the importer relays as a
# wall of API-error, response-body and could-not-complete lines per account. None of
# them say "re-authorise", which is the only thing the reader needs to know.
CONSENT_EXPIRED = re.compile(r"CLOSED_SESSION|Session is closed")
# The echo of that one failure: Enable Banking is asked for each account in turn and
# every call fails the same way, so one dead consent yields several API-error,
# response-body and could-not-complete lines. They arrive through the production.ERROR
# branch, not CONSOLE_ERROR, which is why suppressing them needs its own pattern.
EB_FAILURE_ECHO = re.compile(r"Enable Banking API error|Response body:"
                             r"|Could not complete import from file")
# Failures the CLI prints to the console only, without a matching ERROR log line.
CONSOLE_ERROR = re.compile(r"Too many errors in the data conversion|^Error message: "
                           r"|requisition|No access, or no connection"
                           r"|is not a valid location|not in the list of allowed paths"
                           r"|There are no files in directory")
attempted, finished = [], []
per_config = {}   # config file -> error lines logged while that config ran
exit_code = None  # printed by the job's wrapper after the CLI returns
run_seen = False
try:
    sa   = "/var/run/secrets/kubernetes.io/serviceaccount"
    ns   = open(f"{sa}/namespace").read().strip()
    ktok = open(f"{sa}/token").read().strip()
    ctx  = ssl.create_default_context(cafile=f"{sa}/ca.crt")
    # Python 3.13 enables VERIFY_X509_STRICT, which rejects the cluster CA because
    # it carries no keyUsage extension. Drop just that check - the certificate is
    # still verified against the cluster CA, we are not disabling verification.
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    def k8s(path):
        r = urllib.request.Request("https://kubernetes.default.svc" + path,
                                   headers={"Authorization": "Bearer " + ktok})
        with urllib.request.urlopen(r, context=ctx, timeout=30) as resp:
            return resp.read().decode("utf-8", "replace")
    pods = json.loads(k8s(f"/api/v1/namespaces/{ns}/pods?labelSelector=component%3Dautoimport"))
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(hours=RUN_MAX_AGE)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # RFC 3339 UTC timestamps in a fixed format compare correctly as strings.
    recent = sorted((p for p in pods["items"]
                     if p["metadata"]["creationTimestamp"] >= cutoff),
                    key=lambda p: p["metadata"]["creationTimestamp"])
    if recent:
        run_seen = True
        pod  = recent[-1]
        log  = k8s(f"/api/v1/namespaces/{ns}/pods/{pod['metadata']['name']}/log")
        # The importer processes one config at a time, and every line between two
        # "Going to convert" markers belongs to that config, which is what allows
        # the empty-download noise to be judged and suppressed per account below.
        #
        # A config can fail BEFORE its "Going to convert" line: the importer first
        # fetches the account details from the bank, and an error there aborts the
        # config with only 'Could not complete import from file "..."' naming it.
        # So errors after a "Done!" are held as unassigned until a line names
        # their config, instead of being charged to the config that just finished.
        UNASSIGNED = "(between configs)"
        def assign_held(cfg):
            per_config[cfg] = per_config.pop(UNASSIGNED, []) + per_config.get(cfg, [])
        current = UNASSIGNED
        for ln in log.splitlines():
            m = re.search(r"Going to convert from file .* using configuration (\S+) and flow", ln)
            if m:
                current = os.path.basename(m.group(1))
                attempted.append(current)
                assign_held(current)
            # Switch on any config this line names that is not the current one. Keying
            # on "unassigned" instead missed the case that matters: only a "Done!"
            # clears `current`, so when a config FAILS the next config's pre-conversion
            # errors were charged to the one that failed. With two expired consents on
            # 2026-10-24 the mail blamed c24.json for Comdirect and left Comdirect out
            # of the aborted list entirely - i.e. it would have named the wrong bank.
            m = re.search(r'Could not complete import from file "([^"]+)"', ln)
            if m and os.path.basename(m.group(1)) != current:
                current = os.path.basename(m.group(1))
                attempted.append(current)
                assign_held(current)
            if ln.strip() == "Done!":
                finished.append(current)
                current = UNASSIGNED
            m = re.match(r"importer exit code: (\d+)", ln)
            if m: exit_code = int(m.group(1))
            if "production.ERROR" in ln and not any(x in ln for x in NOT_A_FAILURE):
                msg = re.sub(r"^\[[^\]]+\] production.ERROR: ", "", ln).strip()[:180]
                per_config.setdefault(current, []).append(msg)
            elif CONSOLE_ERROR.search(ln):
                per_config.setdefault(current, []).append(ln.strip()[:180])
        if exit_code is None:
            per_config.setdefault("(run)", []).append(
                f"import run did not finish (pod {pod['metadata']['name']} is "
                f"{pod['status'].get('phase')}) - killed by the deadline or OOM?")
except Exception as e:                       # never let monitoring break the digest
    per_config.setdefault("(digest)", []).append(f"(could not read importer log: {e})")

real_errors = []
# Configs that ended early only because their bank had nothing new. The CLI
# prints no "Done!" for them, yet nothing failed.
quiet = set()
# Judged per run, not per config. Enable Banking is asked for each account in turn, so
# the one line that actually says CLOSED_SESSION can land against whichever config was
# current at the time, while the config it belongs to only shows the echo afterwards.
# Both configs were hit on 2026-10-24 yet only c24 carried the telling line.
run_consent_expired = any(CONSENT_EXPIRED.search(m)
                          for ms in per_config.values() for m in ms)
for cfg, msgs in per_config.items():
    empty = any(ZERO_DOWNLOAD.search(m) for m in msgs)
    if run_consent_expired and any(EB_FAILURE_ECHO.search(m) or CONSENT_EXPIRED.search(m)
                                   for m in msgs):
        real_errors.append(
            f"{cfg}: Enable Banking consent has EXPIRED (the run reported a closed "
            f"session). Only an interactive re-authorisation fixes this - see the steps "
            f"at the end of this mail.")
        # Everything else this config logged is the same failure echoed per account.
        msgs = [m for m in msgs
                if not CONSENT_EXPIRED.search(m) and not EB_FAILURE_ECHO.search(m)]
    for m in msgs:
        if empty and ZERO_DOWNLOAD.search(m):
            continue
        hit = TOO_MANY.search(m)
        if empty and hit and hit.group(1) == "1":
            quiet.add(cfg)
            continue
        # Name the config. Several banks are imported in one run, and an error
        # on its own does not say which connection needs attention.
        real_errors.append(m if cfg.startswith("(") else f"{cfg}: {m}")

aborted = [c for c in dict.fromkeys(attempted) if c not in finished and c not in quiet]
# Without a run to read, "no errors found" would be a lie. Say so instead of
# implying everything is fine.
if not run_seen and "(digest)" not in per_config:
    real_errors.append(f"no auto-import run in the last {RUN_MAX_AGE}h "
                       "(CronJob suspended, or its pod never started?)")
elif run_seen and not attempted and not real_errors:
    real_errors.append("the auto-import ran but processed no config")
# collapse repeats, keep order
real_errors = list(dict.fromkeys(real_errors))[:12]

# A PSD2 bank consent dies on its own after ~90 days and takes the nightly import with
# it, silently: the log only complains once it is already too late. The downloaded
# config carries the authorisation time, so the expiry is predictable to the day, which
# turns a multi-day outage into advance notice. Measured against created_at because
# that is when the SCA happened; c24 and comdirect were both authorised 2026-07-25 and
# both stopped working on day 91.
#
# Wrapped like the log read: monitoring must never be the thing that breaks.
#
# Each warned config is also attached in a form that can be uploaded straight back: the
# live file cannot be, because the importer validates the dead session before it offers
# the bank login and answers HTTP 500. Clearing the session fields makes it acceptable
# again while keeping every other setting, which is the whole point - starting from a
# blank import means the configuration is built before the flow is known, and that
# branch defaults duplicate detection to content-based. The cleared fields are also the
# only ones in these files that are not plain settings, so the attachment carries no
# credential: the Enable Banking app id and key live in the importer's own environment.
RESET_FIELDS = {"accounts": {}, "enable_banking_auth_id": "", "enable_banking_sessions": []}
expiring = []
resets = []      # (filename, bytes) per warned config, attached to the mail
try:
    import glob
    for path in sorted(glob.glob("/import/*.json")):
        try:
            cfg = json.load(open(path))
        except Exception:
            continue                      # a .bak or half-written file is not our problem
        if cfg.get("flow") != "eb" or not cfg.get("created_at"):
            continue                      # only Enable Banking configs hold a consent
        authorised = datetime.datetime.fromisoformat(cfg["created_at"]).date()
        left = (authorised + datetime.timedelta(days=CONSENT_DAYS) - today).days
        if left <= CONSENT_WARN:
            # Re-authorising means mapping the bank's accounts by hand, the bank offers
            # accounts that are deliberately not tracked, and on that form the only thing
            # telling two of them apart is the IBAN - the display name is the account
            # holder for every one of them. So resolve each mapped Firefly id to its name
            # and IBAN and spell the mapping out. The config's own keys are no help: those
            # are the bank's UUIDs, and they change with every authorisation.
            mapped = [(str(v),) + asset_by_id.get(str(v), ("(unknown in Firefly)", ""))
                      for v in cfg.get("accounts", {}).values()]
            mapped.sort(key=lambda m: m[2] or m[1])
            name = os.path.basename(path)
            expiring.append((name, cfg.get("enable_banking_bank") or "?",
                             authorised.isoformat(), left, mapped))
            resets.append((name[:-len(".json")] + "-reset.json",
                           json.dumps({**cfg, **RESET_FIELDS}, indent=4).encode()))
    expiring.sort(key=lambda e: e[3])
except Exception as e:
    per_config.setdefault("(digest)", []).append(f"(could not read /import: {e})")

# Re-authorising is rare, manual and easy to get subtly wrong, and every one of these
# steps corrects a way a run has actually failed. The warning is the only moment the
# operator thinks about it, so the instructions ride along rather than living somewhere
# that has to be found first. Each step is one line: the mail is read in a client that
# soft-wraps, and hard-wrapped prose pasted anywhere else keeps the breaks.
#
# The link goes on step one rather than over the block, because that is the only step
# with a page to open - everything after it follows from the wizard. /new-import/<flow>
# is the upload form itself, so the mail lands on the field the step talks about instead
# of on the importer's front page.
def reauth_steps(entry, synced):
    # Step six is either a chore or a reassurance, depending on whether the sync job
    # runs here. Telling somebody to copy fields a job already copied invites a second,
    # conflicting edit; staying silent about it when no job runs leaves the import
    # broken while the browser said it worked.
    last = ("6. Nothing further. A job carries the new authorisation into the nightly"
            " configuration before the next import, and mails separately if it refused"
            " to - which it does when a run created accounts instead of mapping them, or"
            " mapped a different set of Firefly accounts than before. Absent such a mail,"
            " the next morning's digest is the confirmation."
            if synced else
            "6. Finally carry created_at, accounts, enable_banking_auth_id and"
            " enable_banking_sessions from the finished job into /import/<config>, leaving"
            " every other field alone; the job is the newest file under"
            " /var/www/html/storage/import-jobs/ in the importer pod, field"
            " 'configuration'. The account UUIDs change at every authorisation, so"
            " swapping only the session leaves a config that finds nothing. The nightly"
            " job reads /import and nothing else: until this is done it keeps failing,"
            " however well the interactive run went.")
    return [
    f"1. Start a new Enable Banking import{f' ({entry})' if entry else ''} and upload the"
    " -reset.json attached to this mail. Uploading the live config instead answers HTTP"
    " 500, because the dead session is validated before the bank login is offered;"
    " starting with no config at all works too, but then duplicate detection begins on"
    " content-based, which re-imports everything the bank re-offers.",
    "2. Confirm the bank, which the uploaded config preselects, then complete the bank's"
    " SCA. That is what mints the new consent; no amount of config editing will.",
    "3. Map the accounts exactly as listed above, going by IBAN. Every account the bank"
    " offers carries the account holder as its display name, so the IBAN is the only thing"
    " telling them apart, and an account not in that list is one we deliberately do not"
    " track - leave it unmapped. The dropdowns start on 'create new account' with a 0.00"
    " balance, so submitted unchanged they create junk asset accounts. Afterwards check"
    " that the number of asset accounts in Firefly is unchanged.",
    "4. Confirm the detection method is still identifier-based on the external id, the"
    " unique column type is external ID, and the date range is 'import everything'. The"
    " form preselects whatever the uploaded config asked for, so this is a check rather"
    " than a change - but it is the setting that decides whether a nightly rolling window"
    " is recognised or imported again.",
    "5. If the submit page says it is running but the progress bar never moves, reload it."
    " The start call is sometimes never sent and the job then sits at waiting_to_start,"
    " which looks exactly like an import that found nothing.",
    last,
    "Lines reading 'There is already a transaction with external_id' are not failures but"
    " the intended duplicate rejections, and a red AxiosError after a finished run is the"
    " last status poll. What counts is the created-transaction messages.",
    ]

subject = f"Firefly import digest {today} - {sum(r[1] for r in rows)} new"
# Zero new transactions is NOT an alarm on its own - a quiet weekend looks
# identical to a broken importer. The per-account staleness check below is the
# reliable signal, so this is only reported as context when we mail anyway.
bits = []
if aborted:     bits.append(f"{len(aborted)} import(s) failed")
if real_errors: bits.append(f"{len(real_errors)} error(s)")
if stale:       bits.append(f"{len(stale)} stale")
if expiring:    bits.append(f"{len(expiring)} consent(s) expiring")
if bits:
    subject = "[!] " + subject + " - " + ", ".join(bits)
    lines += [""]
    if aborted:
        lines += ["Import did NOT complete for:"] + [f"  - {c}" for c in aborted]
    if real_errors:
        lines += ["", "Errors (routine noise excluded):"] + [f"  - {e}" for e in real_errors]
    if expiring:
        lines += ["", "Bank consent expiring - re-authorise before the import breaks:"]
        for cfg, bank, auth, left, mapped in expiring:
            lines += [f"  - {cfg} ({bank}): authorised {auth}, "
                      + (f"EXPIRED {-left} day(s) ago" if left < 0 else
                         "expires TODAY" if left == 0 else f"{left} day(s) left")
                      + (f"; map {len(mapped)} account(s), and nothing else the bank offers:"
                         if mapped else "; maps no account yet")]
            lines += [f"      {iban or '(no IBAN in Firefly)'} -> {aid} {name}"
                      for aid, name, iban in mapped]
    if stale:
        lines += ["", "Silent longer than ever before - check the bank connection:"]
        lines += [f"  - {n}: last {d}, {a} days ago "
                  f"(longest quiet stretch so far {m}d, flags above {l}d)"
                  for n, d, a, m, l in stale]
    if expiring or run_consent_expired:
        lines += ["", "How to re-authorise:"]
        lines += [f"  {s}" for s in
                  reauth_steps(f"{IMPORTER_URL}/new-import/eb" if IMPORTER_URL else "",
                               CONSENT_SYNCED)]

if not bits and not ALWAYS:
    print("nothing to report - no mail sent")
    print("\n".join(lines))
    heartbeat()
    raise SystemExit(0)

msg = EmailMessage()
msg["Subject"] = subject
msg["From"]    = f"{os.environ.get('MAIL_FROM_NAME','Firefly')} <{os.environ['MAIL_FROM_ADDRESS']}>"
msg["To"]      = os.environ["MAIL_DESTINATION"]
msg.set_content("\n".join(lines))
# Only alongside the instructions that tell you to upload them. Attached to every
# digest they would be noise, and on a healthy night there is nothing to re-authorise.
if expiring or run_consent_expired:
    for name, blob in resets:
        msg.add_attachment(blob, maintype="application", subtype="json", filename=name)

with smtplib.SMTP(os.environ["MAIL_HOST"], int(os.environ.get("MAIL_PORT", "587")), timeout=30) as s:
    if os.environ.get("MAIL_ENCRYPTION", "tls") == "tls":
        s.starttls()
    s.login(os.environ["MAIL_USERNAME"], os.environ["MAIL_PASSWORD"])
    s.send_message(msg)
print(subject)
print()
print("\n".join(lines))
heartbeat()
