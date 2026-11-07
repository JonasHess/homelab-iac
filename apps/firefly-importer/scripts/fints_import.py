import csv, datetime, os, re, subprocess, sys, tempfile
from decimal import Decimal, ROUND_HALF_UP

# Fetches a bank account's recent transactions over FinTS (AqBanking) and leaves them in
# the auto-import directory as a CSV in the bank's own web-banking export layout, so the
# 04:00 auto-import books them with the same configuration that booked the history.
#
# Unattended it can only ever read: the bank asks for a TAN on anything beyond its
# exemption window, and nobody is there to type one. That is why the request is always
# limited to the last FINTS_FROM_DAYS days, and why "the bank wants a TAN" is a normal
# outcome reported plainly rather than a crash.
#
# A refused login is the dangerous outcome. Every attempt with a wrong PIN counts toward
# the bank's lockout, and a nightly job would add one per night until the account is
# locked. So a refusal writes a lock file, and no later run talks to the bank until a
# person has removed it.

# The 1822direkt web export's columns, in its order. The importer configuration maps
# them by position, so a reordering here silently books names as amounts.
HEADER = ["Kontonummer", "Datum/Zeit", "Buchungstag", "Wertstellung", "Soll/Haben",
          "Buchungsschl\u00fcssel", "Buchungsart", "Empf\u00e4nger/Auftraggeber Name",
          "Empf\u00e4nger/Auftraggeber IBAN", "Empf\u00e4nger/Auftraggeber BIC",
          "Glaeubiger-ID", "Mandatsreferenz", "Mandatsdatum"] + \
         [f"Vwz.{i}" for i in range(18)] + ["End-to-End-Identifikation"]

# Column list of the AqBanking export profile written by profile_text(); convert() reads
# exactly these names.
# Ten purpose lines: rare bookings run past four, and a line not exported is lost.
PURPOSE_LINES = 10
PROFILE_COLUMNS = ["date", "valutaDate", "value/value", "value/currency", "remoteName[0]",
                   "remoteIban", "remoteBic", "transactionText", "endToEndReference",
                   "mandateId", "creditorSchemeId", "type"] + \
                  [f"purpose[{i}]" for i in range(PURPOSE_LINES)]

# Login refused: wrong PIN, PIN locked, access suspended. 9010 is the generic rejection
# 1822direkt answers a wrong user ID or PIN with.
AUTH_CODES = {"9010", "9931", "9942", "9943", "3938"}
# Strong customer authentication demanded, i.e. a TAN.
SCA_CODES = {"9075", "3956"}


def de_amount(raw):
    """AqBanking's plain decimal as the web export writes it: 1.234,56 with a sign."""
    v = Decimal(raw).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    s = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-" if v < 0 else "") + s


def de_date(iso):
    return datetime.date.fromisoformat(iso).strftime("%d.%m.%Y") if iso else ""


def convert(rows, since):
    """AqBanking export rows -> web-export rows, booked and on or after `since` only.

    Pending rows are left out because the bank rewrites or drops them once booked, and
    zero amounts because they are account statements, which Firefly refuses.
    """
    out = []
    for r in rows:
        if r.get("type") != "statement" or not r.get("date") or r["date"] < since:
            continue
        if Decimal(r["value_value"] or "0") == 0:
            continue
        row = dict.fromkeys(HEADER, "")
        row.update({
            "Buchungstag": de_date(r["date"]),
            "Wertstellung": de_date(r.get("valutaDate", "")),
            "Soll/Haben": de_amount(r["value_value"]),
            "Buchungsart": r.get("transactionText", ""),
            "Empf\u00e4nger/Auftraggeber Name": r.get("remoteName", ""),
            "Empf\u00e4nger/Auftraggeber IBAN": r.get("remoteIban", ""),
            "Empf\u00e4nger/Auftraggeber BIC": r.get("remoteBic", ""),
            "Glaeubiger-ID": r.get("creditorSchemeId", ""),
            "Mandatsreferenz": r.get("mandateId", ""),
            "End-to-End-Identifikation": r.get("endToEndReference", ""),
        })
        # AqBanking names the exported columns purpose, purpose1, purpose2, ...
        for i in range(PURPOSE_LINES):
            row[f"Vwz.{i}"] = r.get("purpose" if i == 0 else f"purpose{i}", "")
        out.append(row)
    return out


def classify(log, rc):
    """ok | tan_required | auth_failed | error, from AqBanking's output and exit code.

    A refused login outranks everything else: it is the one outcome that must stop all
    further attempts.
    """
    codes = set(re.findall(r"HBCI: (\d{4})", log))
    if codes & AUTH_CODES:
        return "auth_failed"
    if codes & SCA_CODES or "No TAN input in non-interactive mode" in log:
        return "tan_required"
    return "ok" if rc == 0 else "error"


def write_csv(rows, target):
    """Write next to the target and rename, so the auto-import never reads half a file.

    The temp name starts with a dot and lacks the .csv suffix, so the auto-import does
    not pick it up either.
    """
    target = os.fspath(target)
    fd, tmp = tempfile.mkstemp(prefix=".fints-", dir=os.path.dirname(target))
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADER, delimiter=";", quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(rows)
    os.chmod(tmp, 0o644)
    os.replace(tmp, target)


def profile_text():
    cols = "\n".join(f'    {i}="{c}"' for i, c in enumerate(PROFILE_COLUMNS, 1))
    return ('char name="fints-import"\nint import="0"\nint export="1"\nchar type="csv"\n'
            'char groupNames="transaction", "transfer", "debitnote", "line"\n'
            'char dateFormat="YYYY-MM-DD"\nint utc="0"\nint usePosNegField="0"\n'
            'int defaultIsPositive="1"\nchar valueFormat="float"\n'
            'params {\n  quote="1"\n  title="1"\n  delimiter=";"\n  columns {\n'
            f'{cols}\n  }}\n}}\n')


def aq(cfg, *args, pinfile=None, interactive=False):
    cmd = ["aqbanking-cli"] + ([] if interactive else ["-n"]) + ["-A", "-D", cfg]
    if pinfile:
        cmd += ["-P", pinfile]
    cmd += list(args)
    if interactive:
        return subprocess.run(cmd).returncode, ""
    p = subprocess.run(cmd, capture_output=True, text=True)
    out = p.stdout + p.stderr
    print(out, end="")
    return p.returncode, out


def login_id(cfg):
    p = subprocess.run(["aqhbci-tool4", "-D", cfg, "listusers"], capture_output=True, text=True)
    m = re.search(r"User Id: (\S+)", p.stdout)
    if not m:
        sys.exit(f"no AqBanking user in {cfg}: copy the set-up configuration there first")
    return m.group(1)


def main():
    cfg = os.environ["FINTS_CONFIG_DIR"]
    lock = os.path.join(cfg, "LOGIN_REFUSED")
    login_mode = len(sys.argv) > 1 and sys.argv[1] == "login"

    if os.path.exists(lock):
        print(f"{lock} exists: the bank refused the login before. Not contacting it again.\n"
              "Check the PIN in Akeyless, log in once on the bank's website, then delete the "
              "file and run the login job.")
        return 3

    blz, iban = os.environ["FINTS_BLZ"], os.environ["FINTS_IBAN"]
    work = tempfile.mkdtemp()
    pinfile = os.path.join(work, "pin")
    with open(pinfile, "w") as fh:
        fh.write(f'PIN_{blz}_{login_id(cfg)} = "{os.environ["FINTS_PIN"]}"\n')
    ctx = os.path.join(work, "ctx")

    if login_mode:
        # The bank's default window always demands a TAN, which is what renews the
        # strong authentication the nightly short-window requests then live on.
        rc, _ = aq(cfg, "request", "--transactions", "-A", iban, "-c", ctx,
                   pinfile=pinfile, interactive=True)
        print("login", "succeeded" if rc == 0 else f"failed (exit {rc})")
        return rc

    days = int(os.environ.get("FINTS_FROM_DAYS", "30"))
    since_day = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y%m%d")
    rc, log = aq(cfg, "request", "--transactions", "-A", iban, f"--fromdate={since_day}",
                 "-c", ctx, pinfile=pinfile)
    outcome = classify(log, rc)
    if outcome == "auth_failed":
        with open(lock, "w") as fh:
            fh.write(datetime.datetime.now().isoformat() + "\n" + log[-4000:])
        print(f"LOGIN REFUSED by the bank. Wrote {lock}; no further attempts until it is removed.")
        return 4
    if outcome == "tan_required":
        print("TAN REQUIRED: the bank wants a fresh strong authentication. Run the login job:\n"
              + os.environ.get("FINTS_LOGIN_HINT", ""))
        return 5
    if outcome == "error":
        print(f"aqbanking-cli failed (exit {rc}); nothing written.")
        return 6

    profile = os.path.join(work, "profile.conf")
    with open(profile, "w") as fh:
        fh.write(profile_text())
    export = os.path.join(work, "export.csv")
    rc, _ = aq(cfg, "export", "-c", ctx, "--exporter=csv", f"--profile-file={profile}", "-o", export)
    if rc != 0:
        print(f"export failed (exit {rc}); nothing written.")
        return 6
    with open(export, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh, delimiter=";"))
    booked = convert(rows, os.environ["FINTS_SINCE"])
    target = os.environ["FINTS_OUTPUT"]
    write_csv(booked, target)
    print(f"{len(rows)} rows from the bank, {len(booked)} booked rows written to {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
