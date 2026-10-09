import datetime, json, os, re, smtplib, ssl, urllib.error, urllib.request
from email.message import EmailMessage

# Amounts are compared in integer cents throughout. The guard that makes this job
# safe is "the two legs sum to the lump exactly", and float arithmetic cannot
# express that reliably.

def cents_de(s):
    """German-formatted amount ("1.234,56") to integer cents."""
    return int(round(float(s.replace(".", "").replace(",", ".")) * 100))

def cents(s):
    """API-formatted amount ("1234.56") to integer cents."""
    return int(round(float(s) * 100))

def amount(c):
    """Integer cents back to the string the API expects."""
    return f"{c // 100}.{abs(c) % 100:02d}"

# Each bank words the breakdown differently AND orders the two numbers
# differently, so the order is part of the pattern rather than an assumption.
PATTERNS = [
    (re.compile(r"RECHN\.ZINS\s+([\d.]+,\d{2})\s+TILG\./ENTG\.\s+([\d.]+,\d{2})"), "zins_first"),
    (re.compile(r"Tilgung\s+([\d.]+,\d{2})\s+Zinsen\s+([\d.]+,\d{2})"), "tilgung_first"),
]

def breakdown(desc):
    """(zins, tilgung) in cents, or None when no known wording matches."""
    for rx, order in PATTERNS:
        m = rx.search(desc or "")
        if not m:
            continue
        a, b = cents_de(m.group(1)), cents_de(m.group(2))
        return (a, b) if order == "zins_first" else (b, a)
    return None

def plan(groups, by_payee):
    """Decide what to do, without touching anything.

    Returns (actions, problems). An entry only becomes an action when the
    destination is a known loan payee, the wording parses, and the legs sum to
    the lump to the cent. Everything else is either silently none of our
    business or a problem to report - never a guess.

    Kept free of I/O so the guards can be tested directly.
    """
    actions, problems = [], []
    seen = set()
    for g in groups:
        # A group on a page boundary comes back on two consecutive pages, so the
        # same payment would otherwise be split twice, the second attempt failing.
        if g["id"] in seen:
            continue
        seen.add(g["id"])
        splits = g["attributes"]["transactions"]
        if len(splits) != 1:
            continue                      # already split
        s = splits[0]
        loan = by_payee.get(str(s["destination_id"]))
        if loan is None:
            continue
        total = cents(s["amount"])
        date = s["date"][:10]
        parsed = breakdown(s["description"])
        if parsed is None:
            problems.append(f"g{g['id']} {date} {loan['label']}: payment of {amount(total)} to the "
                            f"loan payee, but no known Zins/Tilgung wording in: "
                            f"{(s['description'] or '')[:120]!r}")
            continue
        zins, tilg = parsed
        if zins + tilg != total:
            problems.append(f"g{g['id']} {date} {loan['label']}: Zins {amount(zins)} + Tilgung "
                            f"{amount(tilg)} != lump {amount(total)} - left alone")
            continue
        actions.append({"group": g["id"], "date": date, "loan": loan, "total": total,
                        "zins": zins, "tilgung": tilg, "source_id": str(s["source_id"]),
                        "category": s.get("category_name") or "",
                        "external_id": s.get("external_id") or ""})
    return actions, problems

def api(path, method="GET", payload=None):
    base = os.environ["FIREFLY_III_URL"].rstrip("/")
    token = os.environ["FIREFLY_III_ACCESS_TOKEN"]
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        base + path, data=body, method=method,
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/json",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r) if r.status != 204 else {}

def split_payload(a):
    date, loan = a["date"], a["loan"]
    de = f"{date[8:10]}.{date[5:7]}.{date[:4]}"
    return {
        "group_title": f"Teilzahlung Darlehen {loan['label']} {de}",
        "transactions": [
            # The external_id rides on the Tilgung leg: replacing the splits would
            # otherwise drop it, and while the row is still inside the importer's
            # re-fetch window that makes the bank's lump look unseen and reappear.
            {"type": "withdrawal", "date": date, "amount": amount(a["tilgung"]),
             "source_id": a["source_id"], "destination_id": str(loan["liability"]),
             "description": f"Darlehen {loan['label']} Tilgung ({de})",
             "category_name": a["category"], "external_id": a["external_id"]},
            {"type": "withdrawal", "date": date, "amount": amount(a["zins"]),
             "source_id": a["source_id"], "destination_id": str(loan["interest"]),
             "description": f"Darlehen {loan['label']} Zins ({de})",
             "category_name": a["category"]},
        ]}

def liability_balance(account_id):
    return float(api(f"/api/v1/accounts/{account_id}")["data"]["attributes"]["current_balance"])

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
    loans    = json.loads(os.environ.get("LOAN_SPLIT_LOANS", "[]"))
    lookback = int(os.environ.get("LOAN_SPLIT_LOOKBACK_DAYS", "45"))
    dry_run  = os.environ.get("LOAN_SPLIT_DRY_RUN", "false").lower() == "true"
    by_payee = {str(l["payee"]): l for l in loans}

    today = datetime.date.today()
    since = (today - datetime.timedelta(days=lookback)).isoformat()
    groups, page = [], 1
    while True:
        d = api(f"/api/v1/transactions?start={since}&end={today.isoformat()}"
                f"&type=withdrawal&limit=500&page={page}")
        groups.extend(d["data"])
        if page >= d["meta"]["pagination"]["total_pages"]:
            break
        page += 1

    actions, problems = plan(groups, by_payee)
    done, skipped = [], []

    for a in actions:
        label = f"g{a['group']} {a['date']} {a['loan']['label']}"
        if dry_run:
            skipped.append(f"{label}: would split {amount(a['total'])} into "
                           f"Zins {amount(a['zins'])} / Tilgung {amount(a['tilgung'])}")
            continue
        before = liability_balance(a["loan"]["liability"])
        try:
            api(f"/api/v1/transactions/{a['group']}", "PUT", split_payload(a))
        except urllib.error.HTTPError as e:
            problems.append(f"{label}: PUT failed HTTP {e.code} "
                            f"{e.read()[:200].decode(errors='replace')}")
            continue
        # The split is only correct if the debt actually fell by the Tilgung.
        # Anything else means a leg landed somewhere other than the liability.
        after = liability_balance(a["loan"]["liability"])
        moved = cents(str(round(after - before, 2)))
        if moved != a["tilgung"]:
            problems.append(f"{label}: split written, but the liability moved {amount(moved)} "
                            f"instead of {amount(a['tilgung'])} - CHECK THIS")
        else:
            done.append(f"{label}: Zins {amount(a['zins'])} / Tilgung {amount(a['tilgung'])}, "
                        f"debt now {after:,.2f}")

    for line in done:     print("split   :", line)
    for line in skipped:  print("dry-run :", line)
    for line in problems: print("PROBLEM :", line)

    if not (done or problems or skipped):
        print("nothing to split - no mail sent")
        return 0

    bits = []
    if done:     bits.append(f"{len(done)} split")
    if problems: bits.append(f"{len(problems)} needing attention")
    if skipped:  bits.append(f"{len(skipped)} dry-run")
    body = ""
    if done:     body += "Split:\n"                + "".join(f"  - {l}\n" for l in done) + "\n"
    if skipped:  body += "Dry run, not written:\n" + "".join(f"  - {l}\n" for l in skipped) + "\n"
    if problems: body += "Needs attention:\n"      + "".join(f"  - {l}\n" for l in problems) + "\n"

    if not os.environ.get("MAIL_HOST"):
        print("no MAIL_HOST configured - not mailing")
    else:
        send_mail("Firefly loan splits: " + ", ".join(bits), body)
        print("mail sent")
    return 1 if problems else 0

if __name__ == "__main__":
    raise SystemExit(main())
