import datetime, json, os, re, smtplib, ssl, urllib.error, urllib.request
from collections import defaultdict
from email.message import EmailMessage

# A move between two of your own accounts is reported by both banks, and neither knows
# about the other. One side arrives as a withdrawal to a payee that is really your own
# account, the other as an unrelated deposit. Net worth stays right, which is why it
# goes unnoticed, while income and expenses are each overstated by the full amount -
# for a monthly standing transfer that is twelve times a year in both directions.
#
# This job joins the halves into the single transfer they always were. It writes, so it
# pairs on stricter grounds than the nightly check that reports them, and it refuses
# wherever the pairing is not unique.

# Amounts compared in integer cents: the pairing guard is "both halves are the same
# amount", and float equality cannot carry that.

def cents(s):
    return int(round(float(s) * 100))


def amount(c):
    return f"{c / 100:,.2f}"


def dedupe(groups):
    """Drop repeated groups.

    The paginated listing returns a group sitting on a page boundary twice, which here
    would offer the same half for pairing two times over.
    """
    seen, out = set(), []
    for g in groups:
        if g["id"] in seen:
            continue
        seen.add(g["id"])
        out.append(g)
    return out


def own_account_in(name, own_by_iban):
    """The own account whose IBAN appears in a payee name, or None.

    The importer names such a payee after the counterparty and appends the IBAN when
    the plain name is already taken, so the IBAN is embedded in free text rather than
    held in the payee's own iban field. Punctuation is dropped before matching because
    the bank's spacing is not stable.
    """
    digits = re.sub(r"[^A-Z0-9]", "", (name or "").upper())
    for iban, acct in own_by_iban.items():
        if iban and iban in digits:
            return acct
    return None


def plan(groups, own_by_iban):
    """Pair half-booked own-account transfers.

    own_by_iban maps IBAN -> {"id", "name"} for accounts Firefly itself holds an IBAN
    for. Accounts whose IBAN is only known from configuration are deliberately out of
    scope: this job deletes a transaction, so it acts only where the ledger itself
    carries the evidence.

    Returns (pairs, refusals). A refusal is a half that looks pairable but cannot be
    paired safely; it is reported so a human can look. Halves with no counterpart at
    all are left out of both - the nightly check already reports those, and a writing
    job repeating them every night would train you to ignore its mail.
    """
    # Keyed by what must match for two halves to be the same movement.
    wds, dps = defaultdict(list), defaultdict(list)
    refusals = []

    for g in groups:
        txs = g["attributes"]["transactions"]
        # A split transaction has deliberate structure. Converting or deleting one is
        # never a mechanical fix.
        if len(txs) != 1:
            continue
        s = txs[0]
        if s["type"] == "withdrawal":
            target = own_account_in(s.get("destination_name"), own_by_iban)
            if not target:
                continue
            if str(s.get("source_id")) == target["id"]:
                refusals.append(f"g{g['id']} {s['date'][:10]} {amount(cents(s['amount']))}: "
                                f"payee carries the IBAN of its own source account")
                continue
            wds[(s["date"][:10], cents(s["amount"]), target["id"])].append((g["id"], s))
        elif s["type"] == "deposit":
            dest = str(s.get("destination_id"))
            if dest in {a["id"] for a in own_by_iban.values()}:
                dps[(s["date"][:10], cents(s["amount"]), dest)].append((g["id"], s))

    pairs = []
    for key, ws in sorted(wds.items()):
        date, amt, target_id = key
        ds = dps.get(key, [])
        if not ds:
            continue                      # reported by the nightly check, not by this job
        if len(ws) > 1 or len(ds) > 1:
            refusals.append(
                f"{date} {amount(amt)} -> account {target_id}: {len(ws)} withdrawal(s) and "
                f"{len(ds)} deposit(s) match, too ambiguous to join "
                f"(groups {sorted(g for g, _ in ws)} / {sorted(g for g, _ in ds)})")
            continue
        (wgid, w), (dgid, d) = ws[0], ds[0]
        pairs.append({"date": date, "cents": amt,
                      "drop": wgid, "drop_split": w,
                      "keep": dgid, "keep_split": d,
                      "source_id": str(w["source_id"]), "source_name": w["source_name"],
                      "target_id": target_id, "target_name": d["destination_name"]})
    return pairs, refusals


def api(path, method="GET", payload=None):
    base = os.environ["FIREFLY_III_URL"].rstrip("/")
    token = os.environ["FIREFLY_III_ACCESS_TOKEN"]
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        base + path, data=data, method=method,
        headers={"Authorization": "Bearer " + token, "Accept": "application/json",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read()
        return json.loads(body) if body else {}


def transfer_payload(pair):
    """Turn the surviving deposit into the transfer it always was.

    The deposit is the half that is kept because it is the one the receiving bank
    reported, and that side carries the stable external_id - the paying side's id is
    derived from the statement position and is of no use once the row is gone.

    The description is left as the bank wrote it. A job has nothing better to put there,
    and inventing a label would hide what the statement actually said.
    """
    return {"transactions": [{
        "transaction_journal_id": pair["keep_split"]["transaction_journal_id"],
        "type": "transfer",
        "source_id": pair["source_id"],
        "destination_id": pair["target_id"],
    }]}


def balance(account_id):
    a = api(f"/api/v1/accounts/{account_id}")["data"]["attributes"]
    return cents(a["current_balance"])


def send_mail(subject, body):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{os.environ.get('MAIL_FROM_NAME','Firefly')} <{os.environ['MAIL_FROM_ADDRESS']}>"
    msg["To"] = os.environ["MAIL_DESTINATION"]
    msg.set_content(body)
    with smtplib.SMTP(os.environ["MAIL_HOST"], int(os.environ.get("MAIL_PORT", "587")), timeout=30) as sm:
        if os.environ.get("MAIL_ENCRYPTION", "tls") == "tls":
            sm.starttls(context=ssl.create_default_context())
        sm.login(os.environ["MAIL_USERNAME"], os.environ["MAIL_PASSWORD"])
        sm.send_message(msg)


def main():
    window  = int(os.environ.get("LINK_TRANSFERS_WINDOW_DAYS", "90"))
    dry_run = os.environ.get("LINK_TRANSFERS_DRY_RUN", "false").lower() == "true"

    own_by_iban = {}
    for kind in ("asset", "liabilities"):
        for a in api(f"/api/v1/accounts?type={kind}&limit=200")["data"]:
            iban = re.sub(r"[^A-Z0-9]", "", (a["attributes"].get("iban") or "").upper())
            if iban:
                own_by_iban[iban] = {"id": a["id"], "name": a["attributes"]["name"]}

    today = datetime.date.today()
    since = (today - datetime.timedelta(days=window)).isoformat()
    groups, page = [], 1
    while True:
        d = api(f"/api/v1/transactions?start={since}&end={today.isoformat()}"
                f"&limit=500&page={page}")
        groups.extend(d["data"])
        if page >= max(1, d["meta"]["pagination"]["total_pages"]):
            break
        page += 1
    groups = dedupe(groups)

    pairs, refusals = plan(groups, own_by_iban)
    print(f"window {since} .. {today}  ({len(groups)} groups, {len(own_by_iban)} own IBANs)")
    print(f"pairs to join: {len(pairs)}   refused: {len(refusals)}")

    done = []
    for p in pairs:
        label = (f"{p['date']} {amount(p['cents'])} {p['source_name']} -> {p['target_name']} "
                 f"(keep g{p['keep']}, drop g{p['drop']})")
        if dry_run:
            done.append("would join " + label)
            continue
        before_src, before_dst = balance(p["source_id"]), balance(p["target_id"])
        # Convert first, delete second. Either order has a window in which the ledger is
        # wrong, but they fail differently: a conversion without the deletion debits the
        # source twice and still leaves the self-payee withdrawal that the nightly check
        # reports, while a deletion without the conversion leaves a plain deposit that
        # looks like genuine income and nothing would ever flag it again.
        try:
            api(f"/api/v1/transactions/{p['keep']}", "PUT", transfer_payload(p))
        except urllib.error.HTTPError as e:
            refusals.append(f"{label}: PUT failed HTTP {e.code} "
                            f"{e.read()[:200].decode(errors='replace')}")
            continue
        try:
            api(f"/api/v1/transactions/{p['drop']}", "DELETE")
        except urllib.error.HTTPError as e:
            refusals.append(f"{label}: converted, but DELETE failed HTTP {e.code} - the "
                            f"source account is now debited twice, FIX THIS BY HAND")
            continue
        # Joining two halves moves no money, so both balances must be exactly what they
        # were. Anything else means a leg landed on an account nobody chose.
        after_src, after_dst = balance(p["source_id"]), balance(p["target_id"])
        if (after_src, after_dst) != (before_src, before_dst):
            refusals.append(
                f"{label}: joined, but balances moved - source {amount(before_src)} -> "
                f"{amount(after_src)}, destination {amount(before_dst)} -> "
                f"{amount(after_dst)} - CHECK THIS")
        else:
            done.append("joined " + label)

    for line in done:     print("join    :", line)
    for line in refusals: print("PROBLEM :", line)

    if not (done or refusals):
        print("no half-booked own-account transfers to join")
        return 0

    bits = []
    if done:     bits.append(f"{len(done)} dry-run" if dry_run else f"{len(done)} joined")
    if refusals: bits.append(f"{len(refusals)} needing attention")
    body = ""
    if done and dry_run:
        body += "Dry run, nothing written:\n" + "".join(f"  - {l}\n" for l in done) + "\n"
    elif done:
        body += "Joined into a single transfer:\n" + "".join(f"  - {l}\n" for l in done) + "\n"
    if refusals:
        body += "Needs attention:\n" + "".join(f"  - {l}\n" for l in refusals) + "\n"

    if not os.environ.get("MAIL_HOST"):
        print("no MAIL_HOST configured - not mailing")
    else:
        send_mail("Firefly transfer join: " + ", ".join(bits), body)
        print("mail sent")
    return 1 if refusals else 0


if __name__ == "__main__":
    raise SystemExit(main())
