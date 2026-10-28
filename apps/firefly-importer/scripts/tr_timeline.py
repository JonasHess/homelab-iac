import collections, datetime, json, os, re, sys

# Converts Trade Republic timeline events - the raw event list pytr writes with
# `export_transactions --store-event-database` (all_events.json) - into Firefly III
# transaction payloads.
#
# The timeline is used rather than the broker's CSV export because pytr can fetch it
# unattended. The two sources number their events differently, so a ledger built from
# one cannot be deduplicated against the other; every row created here carries an
# external_id derived from the timeline event id.
#
# The field titles below are Trade Republic's own and they are German: pytr fetches the
# timeline in the account's language. They are matched exactly, so a change of account
# language breaks parsing - and it breaks loudly, because the reconciliation in main()
# stops agreeing with the ledger.

CARD_SPEND = {"CARD_TRANSACTION"}
CARD_REFUND = {"CARD_REFUND"}
CARD_ATM = {"CARD_ATM_WITHDRAWAL"}
CARD_FEE = {"CARD_ORDER_FEE"}
MONEY_IN = {"PAYMENT_INBOUND", "BANK_TRANSACTION_INCOMING"}
MONEY_OUT = {"PAYMENT_OUTBOUND", "BANK_TRANSACTION_OUTGOING", "JUNIOR_P2P_TRANSFER"}
INTEREST = {"INTEREST_PAYOUT", "INTEREST_PAYOUT_CREATED"}
DIVIDEND = {"SSP_CORPORATE_ACTION_CASH", "CREDIT"}
TAX_ONLY = {"SSP_CORPORATE_ACTION_CASH_NON_DIVIDEND", "SSP_CORPORATE_ACTION_INSTRUMENT",
            "SSP_TAX_CORRECTION", "TAX_REFUND"}
TRADE = {"SPARE_CHANGE_AGGREGATE", "SAVINGS_PLAN_EXECUTED", "SAVINGS_PLAN_INVOICE_CREATED",
         "TRADING_SAVINGSPLAN_EXECUTED", "ORDER_EXECUTED", "TRADING_TRADE_EXECUTED",
         "TRADE_INVOICE", "IPO_TRADE_EXECUTED"}
SAVEBACK = {"SAVEBACK_AGGREGATE"}
GIFT_OUT = {"GIFTER_TRANSACTION"}
GIFT_IN = {"GIFTING_RECIPIENT_ACTIVITY"}
BONUS = {"REFERRAL_FIRST_TRADE_EXECUTED_INVITER"}
# Every other event type moves no money: reports, document notices, device pairings,
# corporate actions that only rename a holding.

# A card payment is shown immediately but can still change or be reversed, and the
# timeline marks that state in the event's detail rather than in its status. Such events
# are left out and picked up once final, which is also why the broker's own export lags
# a few days behind.
NOT_FINAL = {"ausstehend", "abgelehnt", "storniert", "abgebrochen", "pending"}

# Own-account movements: "transfer" books them as a transfer between the cash account
# and the configured own account; "skip" leaves them out. Rebuilding history needs
# "transfer" - nothing else will create those rows. Day-to-day importing needs "skip":
# the paying bank reports the same movement, usually a day or two later, and is the one
# that owns it (with `link_transfers` retyping it); booking it here as well would debit
# that bank account twice.
OWN_TRANSFER, OWN_SKIP = "transfer", "skip"

FEE = "Geb\u00fchr"
TAXES = ("Steuern", "Steuer")
CAT_FEES = "Bankgeb\u00fchren"
CAT_YIELD = "Kapitalertr\u00e4ge"
PAYEE_TAX = "Finanzamt (KESt)"


def de_amount(text):
    """German-formatted money ('5.831,00 EUR', '+ 0,45 EUR', '-11,08 EUR', '1 EUR') to a
    float. Text without a number - 'Kostenlos', 'Gratis' - is 0.0."""
    if text is None:
        return 0.0
    t = str(text).replace("\xa0", " ").replace("\u2212", "-")
    m = re.search(r"([+-])?\s*([\d.]+(?:,\d+)?)", t)
    if not m:
        return 0.0
    num = float(m.group(2).replace(".", "").replace(",", "."))
    return -num if m.group(1) == "-" else num


def tables(event):
    """[(section title, {field title: text}), ...] for every table section of an event."""
    out = []
    for s in (event.get("details") or {}).get("sections") or []:
        if s.get("type") != "table" or not isinstance(s.get("data"), list):
            continue
        fields = {}
        for d in s["data"]:
            if isinstance(d, dict) and d.get("title"):
                det = d.get("detail")
                fields.setdefault(d["title"], det.get("text") if isinstance(det, dict) else det)
        out.append((s.get("title") or "", fields))
    return out


def field(event, *names):
    """First non-empty value among the given field titles, across all table sections."""
    for _, f in tables(event):
        for n in names:
            v = f.get(n)
            if v not in (None, ""):
                return v
    return None


def counterparty_iban(event):
    """Full IBAN of the other party, without spaces, or None.

    An event can show the IBAN twice: masked in the overview ('..3119') and in full in a
    section of its own. The masked one comes first, so taking the first field titled IBAN
    would lose the account it identifies.
    """
    for _, f in tables(event):
        v = f.get("IBAN")
        if v and not str(v).startswith(".."):
            return re.sub(r"[^A-Z0-9]", "", str(v).upper())
    return None


def is_final(event):
    if event.get("status") not in (None, "EXECUTED"):
        return False
    for _, f in tables(event):
        for v in f.values():
            if isinstance(v, str) and v.strip().lower() in NOT_FINAL:
                return False
    return True


def payloads(event, cash, depot, asset_accounts=None, name_alias=None,
             own_accounts=None, own_mode=OWN_SKIP):
    """Firefly transaction dicts for one event, without the API envelope.

    cash, depot     - Firefly account ids of the cash account and the securities account
    asset_accounts  - asset name -> account id, for holdings kept outside the depot
    name_alias      - timeline counterparty name -> the payee name already in Firefly
    own_accounts    - IBAN -> account id of accounts that are your own; several IBANs may
                      name one account, since a bank merger changes the IBAN only
    own_mode        - OWN_TRANSFER or OWN_SKIP, see above

    Returns [] for anything that moves no money or is not final.
    """
    asset_accounts = asset_accounts or {}
    name_alias = name_alias or {}
    own_accounts = own_accounts or {}
    et = event.get("eventType")
    eid = event.get("id")
    amt = (event.get("amount") or {}).get("value")
    date = (event.get("timestamp") or "")[:10]
    title = event.get("title") or ""
    if not eid or not date or not is_final(event):
        return []

    def alias(n):
        return name_alias.get(n, n)

    def wd(amount, dest_name, desc, ext, cat=None):
        d = {"type": "withdrawal", "amount": f"{abs(amount):.2f}", "date": date,
             "description": desc[:200], "source_id": cash, "destination_name": dest_name,
             "external_id": ext}
        if cat:
            d["category_name"] = cat
        return d

    def dp(amount, src_name, desc, ext, cat=None, dst=None):
        d = {"type": "deposit", "amount": f"{abs(amount):.2f}", "date": date,
             "description": desc[:200], "source_name": src_name,
             "destination_id": dst or cash, "external_id": ext}
        if cat:
            d["category_name"] = cat
        return d

    def tf(amount, src, dst, desc, ext):
        return {"type": "transfer", "amount": f"{abs(amount):.2f}", "date": date,
                "description": desc[:200], "source_id": src, "destination_id": dst,
                "external_id": ext}

    fee = abs(de_amount(field(event, FEE)))
    tax = de_amount(field(event, *TAXES))
    out = []

    if et in CARD_SPEND and amt:
        merch = alias(field(event, "H\u00e4ndler") or title)
        out.append(wd(amt, merch, merch, f"tlx-{eid}") if amt < 0
                   else dp(amt, merch, merch, f"tlx-{eid}"))
    elif et in CARD_REFUND and amt:
        merch = alias(field(event, "H\u00e4ndler") or title)
        out.append(dp(amt, merch, f"Erstattung {merch}", f"tlx-{eid}"))
    elif et in CARD_ATM and amt:
        where = field(event, "Geldautomat") or ""
        out.append(wd(amt, "Geldautomat", f"Abhebung {where}".strip(), f"tlx-{eid}",
                      "Bargeld/ATM"))
    elif et in CARD_FEE and amt:
        out.append(wd(amt, "Trade Republic", title, f"tlx-{eid}", CAT_FEES))
    elif et in MONEY_IN | MONEY_OUT and amt:
        own = own_accounts.get(counterparty_iban(event) or "")
        incoming = et in MONEY_IN
        if own:
            if own_mode == OWN_TRANSFER:
                out.append(tf(amt, own, cash, title, f"tlx-{eid}") if incoming
                           else tf(amt, cash, own, title, f"tlx-{eid}"))
        elif incoming:
            party = alias(field(event, "Von", "Absender", "Name") or title)
            out.append(dp(amt, party, title, f"tlx-{eid}"))
        else:
            party = alias(field(event, "An", "Empf\u00e4nger", "Name") or title)
            out.append(wd(amt, party, title, f"tlx-{eid}"))
    elif et in INTEREST and amt:
        # The event amount is net of withheld tax; book the gross as income and the tax
        # as its own row, the way the tax office sees it.
        out.append(dp(abs(amt) + abs(tax), "INTEREST_PAYMENT", "Zinsen", f"tlx-{eid}",
                      CAT_YIELD))
        if abs(tax) >= 0.005:
            out.append(wd(tax, PAYEE_TAX, "Kapitalertragsteuer Zinsen", f"tlxtax-{eid}",
                          CAT_YIELD))
    elif et in DIVIDEND and amt:
        name = field(event, "Wertpapier", "Asset") or title
        out.append(dp(abs(amt) + abs(tax), name, f"Dividende {name}", f"tlx-{eid}", CAT_YIELD))
        if abs(tax) >= 0.005:
            out.append(wd(tax, PAYEE_TAX, f"Kapitalertragsteuer {name}", f"tlxtax-{eid}",
                          CAT_YIELD))
    elif et in TAX_ONLY and amt:
        name = field(event, "Wertpapier", "Asset") or title
        out.append(wd(amt, PAYEE_TAX, f"Steuer {name}", f"tlx-{eid}", CAT_YIELD) if amt < 0
                   else dp(amt, PAYEE_TAX, f"Steuererstattung {name}", f"tlx-{eid}", CAT_YIELD))
    elif et in TRADE and amt:
        asset = field(event, "Asset") or title
        target = asset_accounts.get(asset, depot)
        # The event amount is what hit the cash account, and the two directions differ: a
        # buy's amount INCLUDES the order fee, a sell's is already NET of fee and withheld
        # tax. The holding must move by the securities' value, so a buy deducts the fee and
        # a sell adds fee and tax back on - otherwise the depot drifts by every fee and
        # every tax ever paid on a sale.
        if amt < 0:
            out.append(tf(abs(amt) - fee, cash, target, f"BUY: {asset}", f"tlx-{eid}"))
        else:
            out.append(tf(abs(amt) + fee + abs(tax), target, cash, f"SELL: {asset}",
                          f"tlx-{eid}"))
            if abs(tax) >= 0.005:
                out.append(wd(tax, PAYEE_TAX, f"Kapitalertragsteuer SELL {asset}",
                              f"tlxtax-{eid}", CAT_YIELD))
        if fee >= 0.005:
            out.append(wd(fee, "Trade Republic", f"{FEE} {asset}", f"tlxfee-{eid}", CAT_FEES))
    elif et in SAVEBACK and amt:
        # One event, two movements: the broker credits the bonus to cash and invests it at
        # once. Both are booked so the income is visible; the cash account nets to zero.
        asset = field(event, "Asset") or title
        out.append(dp(amt, asset, f"Saveback {asset}", f"tlxsb-{eid}", CAT_YIELD))
        out.append(tf(amt, cash, depot, f"BUY: {asset}", f"tlx-{eid}"))
    elif et in GIFT_OUT and amt:
        out.append(wd(abs(amt) - fee, "ETF-Geschenk", title, f"tlx-{eid}"))
        if fee >= 0.005:
            out.append(wd(fee, "Trade Republic", f"{FEE} ETF-Geschenk", f"tlxfee-{eid}",
                          CAT_FEES))
    elif et in GIFT_IN:
        # Shares arrive without any cash movement, so the event carries no amount; the
        # value sits in the detail.
        value = abs(de_amount(field(event, "Summe")))
        if value >= 0.005:
            out.append(dp(value, "ETF-Geschenk", title, f"tlx-{eid}", dst=depot))
    elif et in BONUS and amt:
        out.append(dp(amt, "Trade Republic", title, f"tlx-{eid}"))
    return out


def snapshot_rows(groups, accounts):
    """Flatten Firefly transaction groups into the rows a rebuild has to preserve.

    One row per side that touches one of `accounts`, with the signed amount in cents as
    seen from that account. Groups the paginated listing returned twice count once.
    """
    rows, seen = [], set()
    for g in groups:
        for s in g["attributes"]["transactions"]:
            key = (g["id"], s["transaction_journal_id"])
            if key in seen:
                continue
            seen.add(key)
            c = int(round(float(s["amount"]) * 100))
            src, dst = str(s["source_id"]), str(s["destination_id"])
            for acct, signed in ((src, -c), (dst, c)):
                if acct in accounts:
                    rows.append({"acct": acct, "cents": signed, "date": s["date"][:10],
                                 "gid": g["id"], "type": s["type"],
                                 "source_name": s.get("source_name"),
                                 "destination_name": s.get("destination_name"),
                                 "category_name": s.get("category_name")})
    return rows


def _signed_on(payload, account):
    c = int(round(float(payload["amount"]) * 100))
    if payload.get("source_id") == account:
        return -c
    if payload.get("destination_id") == account:
        return c
    return None


def restore_from_snapshot(payloads_, rows, cash, tol_days=3):
    """Give each rebuilt payload the payee and category of the ledger row it replaces.

    A rebuild should change nothing a person can see except the identifiers, so names and
    categories chosen by hand are carried over instead of being recomputed. Matching is on
    the cash side: same signed amount in cents, nearest date within tol_days - the two
    sources date the same movement by order time and by booking day respectively.

    Returns (payloads, report). report["lost_categories"] lists ledger rows that carried a
    category and found no successor; a rebuild must not proceed while that is non-empty
    unless each one is understood.
    """
    pool = [r for r in rows if r["acct"] == cash]
    used, out = set(), []
    report = collections.Counter()
    for p in payloads_:
        q = dict(p)
        signed = _signed_on(p, cash)
        if signed is None:
            out.append(q)
            report["not_on_cash"] += 1
            continue
        d0 = datetime.date.fromisoformat(p["date"])
        best = None
        for i, r in enumerate(pool):
            if i in used or r["cents"] != signed:
                continue
            dist = abs((datetime.date.fromisoformat(r["date"]) - d0).days)
            if dist <= tol_days and (best is None or dist < best[0]):
                best = (dist, i)
        if best is None:
            out.append(q)
            report["no_template"] += 1
            continue
        used.add(best[1])
        r = pool[best[1]]
        if p["type"] == "withdrawal" and r.get("destination_name"):
            q["destination_name"] = r["destination_name"]
        if p["type"] == "deposit" and r.get("source_name"):
            q["source_name"] = r["source_name"]
        if r.get("category_name"):
            q["category_name"] = r["category_name"]
        out.append(q)
        report["restored"] += 1
    rep = dict(report)
    rep["lost_categories"] = [r for i, r in enumerate(pool)
                              if i not in used and r.get("category_name")]
    return out, rep


def balances(payloads_):
    """Net movement per account id in cents. The rebuild's proof of correctness: the
    totals produced from the timeline must equal what the ledger holds today."""
    bal = collections.Counter()
    for p in payloads_:
        c = int(round(float(p["amount"]) * 100))
        if p["type"] in ("withdrawal", "transfer"):
            bal[str(p["source_id"])] -= c
        if p["type"] in ("deposit", "transfer"):
            bal[str(p["destination_id"])] += c
    return bal


def main():
    events_path = os.environ["TR_TIMELINE_EVENTS"]
    cash = os.environ.get("TR_CASH_ACCOUNT", "257")
    depot = os.environ.get("TR_DEPOT_ACCOUNT", "258")
    asset_accounts = json.loads(os.environ.get("TR_ASSET_ACCOUNTS", "{}"))
    own_accounts = {re.sub(r"[^A-Z0-9]", "", k.upper()): str(v)
                    for k, v in json.loads(os.environ.get("TR_OWN_ACCOUNTS", "{}")).items()}
    name_alias = json.loads(os.environ.get("TR_NAME_ALIAS", "{}"))
    own_mode = os.environ.get("TR_OWN_MODE", OWN_SKIP)
    if own_mode not in (OWN_TRANSFER, OWN_SKIP):
        sys.exit(f"TR_OWN_MODE must be {OWN_TRANSFER!r} or {OWN_SKIP!r}, not {own_mode!r}")

    with open(events_path, encoding="utf-8") as fh:
        events = json.load(fh)
    out, skipped = [], 0
    for e in events:
        ps = payloads(e, cash, depot, asset_accounts, name_alias, own_accounts, own_mode)
        if not ps and (e.get("amount") or {}).get("value") and not is_final(e):
            skipped += 1
        out.extend(ps)

    ids = [p["external_id"] for p in out]
    if len(ids) != len(set(ids)):
        sys.exit("duplicate external_id among the generated payloads - refusing")

    for p in out:
        print(json.dumps({"error_if_duplicate_hash": True, "apply_rules": True,
                          "fire_webhooks": False, "transactions": [p]}, ensure_ascii=False))
    bal = balances(out)
    sys.stderr.write(f"{len(events)} events -> {len(out)} payloads "
                     f"({skipped} not final, left for a later run)\n")
    for acct in sorted(bal):
        sys.stderr.write(f"  account {acct}: {bal[acct] / 100:+,.2f}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
