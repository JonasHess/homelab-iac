import datetime, json, os, re, smtplib, ssl, urllib.request
from collections import defaultdict
from email.message import EmailMessage

# Three read-only checks over a window of the ledger. Each one exists because it has
# already caught real money: mis-booked own-account transfers, re-imported duplicates,
# and transactions the auto-categorise rules silently stopped matching.
#
# Nothing here writes. That is deliberate - the point is to be safe enough to run
# nightly and quiet enough that a mail always means something.


def dedupe(groups):
    """Drop repeated groups.

    The paginated transaction listing returns a group sitting on a page boundary on
    two consecutive pages, which would double-count every amount derived from it.
    """
    seen, out = set(), []
    for g in groups:
        if g["id"] in seen:
            continue
        seen.add(g["id"])
        out.append(g)
    return out


def splits(groups):
    for g in groups:
        for s in g["attributes"]["transactions"]:
            yield g["id"], s


def unlinked_transfers(groups, own_ibans, loan_payees):
    """Transfers between two of your own accounts, imported as two unrelated halves.

    Neither bank knows about the other, so one side arrives as a withdrawal to a payee
    that is really your own account and the other as an unrelated deposit. Net worth
    stays right while income and expenses are both overstated, so nothing looks wrong.

    Two signatures, because one alone is not enough: the payee's name carries one of
    your own IBANs, or the payee is a known self-payee whose own account has no IBAN in
    Firefly (which is the case for all three loan accounts).
    """
    hits = []
    for gid, s in splits(groups):
        if s["type"] == "transfer":
            continue
        for role in ("source", "destination"):
            pid = str(s[f"{role}_id"])
            name = s[f"{role}_name"] or ""
            digits = re.sub(r"[^A-Z0-9]", "", name.upper())
            why = None
            if pid in loan_payees:
                why = f"payee #{pid} is a loan account"
            else:
                match = next((lbl for ib, lbl in own_ibans.items() if ib in digits), None)
                if match:
                    why = f"payee name carries the IBAN of {match}"
            if why:
                hits.append(f"g{gid} {s['date'][:10]} {float(s['amount']):>10,.2f} "
                            f"{name[:38]} - {why}")
                break
    return hits


def _norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def duplicates(groups):
    """(definite, review_count).

    DEFINITE: two transactions sharing a non-empty external_id and otherwise identical -
    the same bank row imported twice, which is always wrong.

    REVIEW: same date/type/amount/counterparty/description but *different* external_ids.
    Usually legitimate (four identical small card payments in one day really happen), so
    it is counted rather than listed - listing it would mail noise every night.
    """
    byext, bykey = defaultdict(list), defaultdict(list)
    for gid, s in splits(groups):
        ext = (s.get("external_id") or "").strip()
        cp = s["destination_name"] if s["type"] == "withdrawal" else s["source_name"]
        ident = (s["date"][:10], s["type"], f'{abs(float(s["amount"])):.2f}',
                 cp, _norm(s.get("description"))[:40])
        if ext:
            byext[ext].append((gid, ident))
        bykey[ident].append((gid, ext))

    definite = []
    for ext, rows in byext.items():
        if len(rows) > 1 and len({r[1] for r in rows}) == 1:
            definite.append(f"external_id {ext}: groups {sorted(r[0] for r in rows)} "
                            f"- {rows[0][1][0]} {rows[0][1][2]} {str(rows[0][1][3])[:30]}")
    review = sum(1 for rows in bykey.values()
                 if len(rows) > 1 and len({e for _, e in rows}) > 1)
    return definite, review


def uncategorised(groups):
    """Non-transfer splits with no category, grouped by counterparty.

    Transfers are left out: an own-account move legitimately has no category. A number
    above zero here means a rule stopped matching, which is invisible otherwise - a
    pattern with the spaces stripped out of a payee name never matches and never errors.
    """
    agg = defaultdict(lambda: [0, 0.0])
    for gid, s in splits(groups):
        if s["type"] == "transfer" or s.get("category_name"):
            continue
        cp = s["destination_name"] if s["type"] == "withdrawal" else s["source_name"]
        agg[cp or "(no counterparty)"][0] += 1
        agg[cp or "(no counterparty)"][1] += float(s["amount"])
    return [f"{n:>3}x {tot:>10,.2f}  {cp[:44]}"
            for cp, (n, tot) in sorted(agg.items(), key=lambda x: -x[1][1])]


def api(path):
    base = os.environ["FIREFLY_III_URL"].rstrip("/")
    token = os.environ["FIREFLY_III_ACCESS_TOKEN"]
    req = urllib.request.Request(
        base + path,
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


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
    window = int(os.environ.get("LEDGER_CHECK_WINDOW_DAYS", "90"))
    loan_payees = {str(p) for p in json.loads(os.environ.get("LEDGER_CHECK_LOAN_PAYEES", "[]"))}
    extra_ibans = json.loads(os.environ.get("LEDGER_CHECK_EXTRA_OWN_IBANS", "{}"))

    own_ibans = dict(extra_ibans)
    for kind in ("asset", "liabilities"):
        for a in api(f"/api/v1/accounts?type={kind}&limit=200")["data"]:
            iban = (a["attributes"].get("iban") or "").strip()
            if iban:
                own_ibans[iban] = a["attributes"]["name"]

    today = datetime.date.today()
    since = (today - datetime.timedelta(days=window)).isoformat()
    groups, page = [], 1
    while True:
        d = api(f"/api/v1/transactions?start={since}&end={today.isoformat()}"
                f"&limit=500&page={page}")
        groups.extend(d["data"])
        if page >= d["meta"]["pagination"]["total_pages"]:
            break
        page += 1
    groups = dedupe(groups)

    unlinked = unlinked_transfers(groups, own_ibans, loan_payees)
    definite, review = duplicates(groups)
    uncat = uncategorised(groups)

    print(f"window {since} .. {today}  ({len(groups)} groups, {len(own_ibans)} own IBANs known)")
    print(f"unlinked transfers: {len(unlinked)}   duplicates: {len(definite)} definite "
          f"/ {review} review   uncategorised: {len(uncat)} counterparties")

    sections = []
    if unlinked:
        sections.append(("Own-account transfers booked as two halves", unlinked))
    if definite:
        sections.append(("Duplicate imports (same external_id)", definite))
    if uncat:
        sections.append(("Uncategorised, by counterparty", uncat))

    if not sections:
        print("ledger clean - no mail sent")
        return 0

    for title, lines in sections:
        print(f"\n-- {title}")
        for l in lines:
            print("  ", l)

    body = f"Window: {since} .. {today} ({len(groups)} transaction groups)\n\n"
    for title, lines in sections:
        body += f"{title}:\n" + "".join(f"  - {l}\n" for l in lines) + "\n"
    if review:
        body += (f"({review} same-date/amount groups differ only by external_id - usually "
                 f"legitimate, not listed.)\n")

    bits = []
    if unlinked: bits.append(f"{len(unlinked)} unlinked")
    if definite: bits.append(f"{len(definite)} duplicate")
    if uncat:    bits.append(f"{len(uncat)} uncategorised")

    if not os.environ.get("MAIL_HOST"):
        print("no MAIL_HOST configured - not mailing")
    else:
        send_mail("Firefly ledger check: " + ", ".join(bits), body)
        print("mail sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
