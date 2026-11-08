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
#
# An import that does recognise the counterparty's IBAN books the whole movement as a
# transfer on its own, and the other bank's half then duplicates it. Those halves are
# dropped before any joining.

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


# How far apart the two banks may date one movement. Covers a weekend plus the usual
# one-day clearing gap; a week is already a different payment.
HALF_MATCH_DAYS = 5


# Banks transliterate differently: one writes "Strasse", another "Strase", a third
# keeps the original letter. Comparing every spelling a text could have been reduced to
# lets them meet.
_SPELLINGS = (str.maketrans({"\u00df": "ss", "\u00e4": "ae", "\u00f6": "oe", "\u00fc": "ue"}),
              str.maketrans({"\u00df": "s", "\u00e4": "a", "\u00f6": "o", "\u00fc": "u"}))


def _spellings(text):
    low = (text or "").lower()
    return {re.sub(r"[^a-z0-9]", "", low.translate(t)) for t in _SPELLINGS} - {""}


def same_purpose(a, b):
    """Do two descriptions name the same payment?

    Both banks pass on the payer's remittance text, but the receiving one sometimes
    appends its own wording ("... / Ueberweisung"), so one containing the other counts.
    Compared without spacing and case, which the banks do not keep stable, and across
    the ways a bank may have spelled out an umlaut or sharp s.
    """
    return any(x in y or y in x for x in _spellings(a) for y in _spellings(b))


def holder_name(payee):
    """A payee name without the IBAN the importer appends to tell namesakes apart."""
    name = re.sub(r"\(?\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}\b\)?", "", payee or "")
    return re.sub(r"[^a-z0-9]", "", name.lower())


def transferred_halves(groups, own_by_iban):
    """Halves of a movement whose other import already booked it as a transfer.

    An import that recognises the counterparty's IBAN as one of your own accounts books
    the movement as a complete transfer. The other bank's import, which does not, adds
    its own half as a plain deposit (or withdrawal) on top - the same money counted
    twice. The transfer is kept; the half is dropped.

    A half matches a transfer on account, amount, a date within HALF_MATCH_DAYS and the
    same purpose. The purpose is what separates the duplicate from a genuinely separate
    payment of the same amount, such as two people paying the same monthly sum into a
    shared account. A half that the ordinary pairing can join with a self-payee
    counterpart of its own is never a candidate: that half belongs to a different
    movement. More than one candidate for a transfer is refused, not guessed.

    Returns (drops, refusals). Each drop names the half's group, the account it sits on
    (account_id) and how that account's balance must change in cents once it is gone
    (delta); the transfer's other account must not move.
    """
    ids = {a["id"] for a in own_by_iban.values()}
    singles = [(g["id"], g["attributes"]["transactions"][0]) for g in groups
               if len(g["attributes"]["transactions"]) == 1]

    # Halves that plan() joins with a self-payee counterpart belong to another movement.
    # On a day with two deposits of one amount but a single such withdrawal, the deposit
    # it pairs with is the one paid by the person the withdrawal names: the importer
    # titles the payee after the account holder and appends the IBAN.
    wd_payees, dp_keys = defaultdict(list), set()
    for _, s in singles:
        if s["type"] == "withdrawal":
            target = own_account_in(s.get("destination_name"), own_by_iban)
            if target:
                wd_payees[(s["date"][:10], cents(s["amount"]), target["id"])].append(
                    holder_name(s.get("destination_name")))
        elif s["type"] == "deposit" and str(s.get("destination_id")) in ids:
            dp_keys.add((s["date"][:10], cents(s["amount"]), str(s["destination_id"])))
    dp_by_key = defaultdict(list)
    for gid, s in singles:
        if s["type"] == "deposit" and str(s.get("destination_id")) in ids:
            dp_by_key[(s["date"][:10], cents(s["amount"]), str(s["destination_id"]))].append((gid, s))
    claimed = set()
    for key, payees in wd_payees.items():
        deposits = dp_by_key.get(key, [])
        if len(deposits) <= len(payees):
            claimed.update(gid for gid, _ in deposits)
            continue
        for p in payees:
            claimed.update(gid for gid, s in deposits if holder_name(s.get("source_name")) == p)

    halves = []
    for gid, s in singles:
        if s["type"] == "deposit" and str(s.get("destination_id")) in ids:
            if gid in claimed:
                continue
            halves.append((gid, s, "in", str(s["destination_id"])))
        elif s["type"] == "withdrawal" and str(s.get("source_id")) in ids:
            target = own_account_in(s.get("destination_name"), own_by_iban)
            if target and (s["date"][:10], cents(s["amount"]), target["id"]) in dp_keys:
                continue
            halves.append((gid, s, "out", str(s["source_id"])))

    transfers = sorted(((gid, s) for gid, s in singles if s["type"] == "transfer"
                        and str(s.get("source_id")) in ids
                        and str(s.get("destination_id")) in ids),
                       key=lambda x: x[1]["date"])

    used, drops, refusals = set(), [], []
    for tgid, t in transfers:
        tday = datetime.date.fromisoformat(t["date"][:10])
        amt = cents(t["amount"])
        found = []
        for gid, s, side, acc in halves:
            if gid in used or cents(s["amount"]) != amt:
                continue
            if (side, acc) not in (("in", str(t["destination_id"])), ("out", str(t["source_id"]))):
                continue
            gap = abs((datetime.date.fromisoformat(s["date"][:10]) - tday).days)
            if gap <= HALF_MATCH_DAYS and same_purpose(s.get("description"), t.get("description")):
                found.append((gid, s, side, acc))
        if not found:
            continue
        label = (f"g{tgid} {t['date'][:10]} {amount(amt)} {t.get('source_name')} -> "
                 f"{t.get('destination_name')}")
        if len(found) > 1:
            refusals.append(f"{label}: {len(found)} matching halves "
                            f"({', '.join('g' + f[0] for f in found)}), too ambiguous to drop one")
            continue
        gid, s, side, acc = found[0]
        used.add(gid)
        drops.append({"group": gid, "split": s, "transfer_group": tgid, "date": s["date"][:10],
                      "cents": amt, "account_id": acc,
                      "account_name": s.get("destination_name") if side == "in" else s.get("source_name"),
                      "delta": -amt if side == "in" else amt})
    return drops, refusals


def sole_source_halves(groups, sole_source):
    """Own-account movements whose other side will never be booked.

    Some accounts deliberately never record a movement to or from your own bank
    accounts - a broker account whose import leaves those out because the bank already
    reports them, for instance. For those the bank's row is the only record there will
    ever be, and its payee name carrying that account's IBAN is the whole of the
    available evidence. Both directions occur: a withdrawal to the broker's IBAN is money
    paid in, a deposit from it is money paid out.

    sole_source maps IBAN -> {"id", "name"} and must name only such accounts. It is
    separate from the IBANs read off the ledger precisely because this is a judgement
    about how an account is fed, which no field in Firefly records. Applying this to a
    normally imported account would be wrong: there the counterpart arrives a day or two
    later, and converting early turns one movement into two.

    Converting is also milder than joining: nothing is deleted, only retyped, so the row
    and its external_id survive and the step is reversible.

    Returns (converts, refusals). Each convert names the transfer it becomes (source_id,
    target_id), the bank account whose balance must not move (bank_id) and the change
    the sole account must show in cents (sole_id, sole_delta). A row that does have its
    counterpart on the sole account is refused rather than converted - that means the
    other side was booked after all, and which of the two to keep is not this function's
    call.
    """
    by_iban, converts, refusals = sole_source or {}, [], []
    if not by_iban:
        return converts, refusals
    ids = {a["id"] for a in by_iban.values()}

    singles = []
    for g in groups:
        txs = g["attributes"]["transactions"]
        if len(txs) == 1:
            singles.append((g["id"], txs[0]))

    # What the sole account itself already holds, keyed by the side it would be on.
    on_sole = set()
    for _, s in singles:
        if s["type"] == "deposit" and str(s.get("destination_id")) in ids:
            on_sole.add(("in", s["date"][:10], cents(s["amount"]), str(s["destination_id"])))
        if s["type"] == "withdrawal" and str(s.get("source_id")) in ids:
            on_sole.add(("out", s["date"][:10], cents(s["amount"]), str(s["source_id"])))

    for gid, s in singles:
        if s["type"] == "withdrawal":
            direction, payee, bank = "in", s.get("destination_name"), str(s.get("source_id"))
            bank_name = s.get("source_name")
        elif s["type"] == "deposit":
            direction, payee, bank = "out", s.get("source_name"), str(s.get("destination_id"))
            bank_name = s.get("destination_name")
        else:
            continue
        sole = own_account_in(payee, by_iban)
        if not sole:
            continue
        amt = cents(s["amount"])
        if direction == "in":
            src, src_name, dst, dst_name = bank, bank_name, sole["id"], sole["name"]
        else:
            src, src_name, dst, dst_name = sole["id"], sole["name"], bank, bank_name
        label = f"g{gid} {s['date'][:10]} {amount(amt)} {src_name} -> {dst_name} (payee {payee})"
        if bank == sole["id"]:
            side = "source" if direction == "in" else "destination"
            refusals.append(f"{label}: payee carries the IBAN of its own {side} account")
            continue
        if (direction, s["date"][:10], amt, sole["id"]) in on_sole:
            other = "deposit" if direction == "in" else "withdrawal"
            refusals.append(f"{label}: a matching {other} exists after all, so which half "
                            f"to keep is a decision, not a conversion")
            continue
        converts.append({"group": gid, "split": s, "cents": amt, "date": s["date"][:10],
                         "source_id": src, "source_name": src_name,
                         "target_id": dst, "target_name": dst_name,
                         "bank_id": bank, "sole_id": sole["id"],
                         "sole_delta": amt if direction == "in" else -amt})
    return converts, refusals


def convert_payload(conv):
    """Retype the bank's row as the transfer it always was.

    Only the type and the two accounts change. The description, the category and the
    external_id are the bank's own and stay, so the row remains recognisable to the
    bank import's duplicate check.
    """
    return {"transactions": [{
        "transaction_journal_id": conv["split"]["transaction_journal_id"],
        "type": "transfer",
        "source_id": conv["source_id"],
        "destination_id": conv["target_id"],
    }]}


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

    own_by_iban, names, account_numbers = {}, {}, {}
    for kind in ("asset", "liabilities"):
        for a in api(f"/api/v1/accounts?type={kind}&limit=200")["data"]:
            names[a["id"]] = a["attributes"]["name"]
            account_numbers[a["id"]] = a["attributes"].get("account_number") or ""
            iban = re.sub(r"[^A-Z0-9]", "", (a["attributes"].get("iban") or "").upper())
            if iban:
                own_by_iban[iban] = {"id": a["id"], "name": a["attributes"]["name"]}

    # Accounts whose own side never arrives, named by configuration because nothing in
    # Firefly records how an account is fed. Their IBAN sits in the account number field
    # rather than the IBAN field: in the IBAN field the ordinary pairing would claim it,
    # and converting a lone half of a normally imported account would double-count.
    sole_source, config_errors = {}, []
    for acc in json.loads(os.environ.get("LINK_TRANSFERS_SOLE_SOURCE_ACCOUNTS", "[]")):
        aid = str(acc)
        if aid not in names:
            config_errors.append(f"soleSourceAccounts: {aid} is not an asset or liability "
                                 f"account here")
            continue
        key = re.sub(r"[^A-Z0-9]", "", account_numbers[aid].upper())
        if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", key):
            config_errors.append(f"soleSourceAccounts: account {aid} has no IBAN in its "
                                 f"account number field")
            continue
        if key in own_by_iban:
            config_errors.append(f"soleSourceAccounts: the IBAN of account {aid} is also on "
                                 f"account {own_by_iban[key]['id']} - the ordinary pairing "
                                 f"covers it, remove it from the configuration")
            continue
        sole_source[key] = {"id": aid, "name": names[aid]}

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

    # Duplicate halves first: left in place, a shared account's second deposit of the
    # day makes the ordinary pairing refuse the genuine one as ambiguous.
    drops, drop_refusals = transferred_halves(groups, own_by_iban)
    dropped = {d["group"] for d in drops}
    remaining = [g for g in groups if g["id"] not in dropped]
    pairs, refusals = plan(remaining, own_by_iban)
    converts, conv_refusals = sole_source_halves(remaining, sole_source)
    refusals = config_errors + drop_refusals + refusals + conv_refusals
    print(f"window {since} .. {today}  ({len(groups)} groups, {len(own_by_iban)} own IBANs, "
          f"{len(sole_source)} sole-source)")
    print(f"halves already transferred: {len(drops)}   pairs to join: {len(pairs)}   "
          f"lone halves to convert: {len(converts)}   refused: {len(refusals)}")

    done = []
    for d in drops:
        label = (f"{d['date']} {amount(d['cents'])} on {d['account_name']} (drop g{d['group']}, "
                 f"already booked as transfer g{d['transfer_group']})")
        if dry_run:
            done.append("would drop " + label)
            continue
        before = balance(d["account_id"])
        try:
            api(f"/api/v1/transactions/{d['group']}", "DELETE")
        except urllib.error.HTTPError as e:
            refusals.append(f"{label}: DELETE failed HTTP {e.code} - the movement is still "
                            f"counted twice")
            continue
        # The transfer already carries the movement, so the half's account must change by
        # exactly the half's own amount and nothing else.
        after = balance(d["account_id"])
        if after - before != d["delta"]:
            refusals.append(f"{label}: dropped, but the balance moved {amount(before)} -> "
                            f"{amount(after)} (expected {d['delta'] / 100:+,.2f}) - CHECK THIS")
        else:
            done.append("dropped duplicate half " + label)

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

    for c in converts:
        label = (f"{c['date']} {amount(c['cents'])} {c['source_name']} -> {c['target_name']} "
                 f"(g{c['group']})")
        if dry_run:
            done.append("would convert " + label)
            continue
        before_bank, before_sole = balance(c["bank_id"]), balance(c["sole_id"])
        try:
            api(f"/api/v1/transactions/{c['group']}", "PUT", convert_payload(c))
        except urllib.error.HTTPError as e:
            refusals.append(f"{label}: PUT failed HTTP {e.code} "
                            f"{e.read()[:200].decode(errors='replace')}")
            continue
        # Unlike a join, a conversion DOES move money: the bank side had already been
        # booked and stays as it was, while the sole account now shows the movement for
        # the first time. Any other outcome means the retyping hit the wrong account.
        after_bank, after_sole = balance(c["bank_id"]), balance(c["sole_id"])
        if after_bank != before_bank or after_sole - before_sole != c["sole_delta"]:
            refusals.append(
                f"{label}: converted, but the balances are wrong - bank account "
                f"{amount(before_bank)} -> {amount(after_bank)} (expected unchanged), "
                f"{c['sole_id']} {amount(before_sole)} -> {amount(after_sole)} (expected "
                f"{c['sole_delta'] / 100:+,.2f}) - CHECK THIS")
        else:
            done.append("converted " + label)

    for line in done:     print("fix     :", line)
    for line in refusals: print("PROBLEM :", line)

    if not (done or refusals):
        print("no half-booked own-account transfers to join or convert")
        return 0

    bits = []
    if done:     bits.append(f"{len(done)} dry-run" if dry_run else f"{len(done)} fixed")
    if refusals: bits.append(f"{len(refusals)} needing attention")
    body = ""
    if done and dry_run:
        body += "Dry run, nothing written:\n" + "".join(f"  - {l}\n" for l in done) + "\n"
    elif done:
        body += "Booked as the single transfer it was:\n" + "".join(f"  - {l}\n" for l in done) + "\n"
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
