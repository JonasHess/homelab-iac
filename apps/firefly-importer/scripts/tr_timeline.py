import collections, datetime, json, os, pathlib, re, smtplib, ssl, subprocess, sys, time
import urllib.error, urllib.parse, urllib.request
from email.message import EmailMessage

# Books Trade Republic transactions into Firefly III from the broker's timeline - the
# raw event list pytr writes with `export_transactions` (all_events.json). Runs nightly
# on a saved web session and never logs in itself; see session_alive().
#
# The timeline is used rather than the broker's CSV export because pytr can fetch it
# unattended. Every row carries an external_id derived from the timeline event id, which
# is what makes a re-run add only what is missing.
#
# The field titles below are Trade Republic's own and they are German: pytr fetches the
# timeline in the account's language. They are matched exactly, so a change of account
# language breaks parsing without an error: fees and taxes read as zero, and the depot
# drifts from the broker's figures by every fee and every tax since.

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
KNOWN = (CARD_SPEND | CARD_REFUND | CARD_ATM | CARD_FEE | MONEY_IN | MONEY_OUT | INTEREST
         | DIVIDEND | TAX_ONLY | TRADE | SAVEBACK | GIFT_OUT | GIFT_IN | BONUS)
# Every other event type is expected to move no money: reports, document notices, device
# pairings, corporate actions that only rename a holding. One that does carry an amount is
# a type this reader has never seen, and is reported rather than silently dropped.

# A card payment is shown immediately but can still change or be reversed, and the
# timeline marks that state in the event's detail rather than in its status. Such events
# are left out and picked up once final, which is also why the broker's own export lags
# a few days behind.
NOT_FINAL = {"ausstehend", "abgelehnt", "storniert", "abgebrochen", "pending"}

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


def payloads(event, cash, depot, asset_accounts=None, name_alias=None, own_accounts=None):
    """Firefly transaction dicts for one event, without the API envelope.

    cash, depot     - Firefly account ids of the cash account and the securities account
    asset_accounts  - asset name -> account id, for holdings kept outside the depot
    name_alias      - timeline counterparty name -> the payee name already in Firefly
    own_accounts    - IBANs of your own accounts. Movements to and from them are left out:
                      the other bank reports the same movement and owns it (with
                      `link_transfers` turning it into a transfer); booking it here too
                      would count it twice.

    Returns [] for anything that moves no money or is not final.
    """
    asset_accounts = asset_accounts or {}
    name_alias = name_alias or {}
    own_accounts = set(own_accounts or ())
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
    elif et in MONEY_IN | MONEY_OUT and amt and counterparty_iban(event) not in own_accounts:
        if et in MONEY_IN:
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


def unhandled(event):
    """True for a final event that moves money but is of a type this reader does not know."""
    return (event.get("eventType") not in KNOWN and bool((event.get("amount") or {}).get("value"))
            and is_final(event))


def plan_posts(events, existing_ids, since, cash, depot, asset_accounts=None, name_alias=None,
               own_accounts=None):
    """(payloads to post, unhandled events) for events dated on or after `since`.

    Each row is checked against existing_ids on its own, so a fee or tax row missing
    beside an existing parent is still posted. Older events are left alone entirely: the
    ledger before the window is settled, and a row deleted there by hand stays deleted.
    """
    posts, odd = [], []
    for e in events:
        if (e.get("timestamp") or "")[:10] < since:
            continue
        if unhandled(e):
            odd.append(e)
            continue
        for p in payloads(e, cash, depot, asset_accounts, name_alias, own_accounts):
            if p["external_id"] not in existing_ids:
                posts.append(p)
    return posts, odd


def api(path, method="GET", payload=None, params=None):
    base = os.environ["FIREFLY_III_URL"].rstrip("/")
    q = "?" + urllib.parse.urlencode(params) if params else ""
    req = urllib.request.Request(
        base + path + q, method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": f"Bearer {os.environ['FIREFLY_III_ACCESS_TOKEN']}",
                 "Accept": "application/vnd.api+json", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def existing_external_ids(accounts, since):
    ids = set()
    for acct in accounts:
        page = 1
        while True:
            params = {"limit": 500, "page": page}
            if since:
                # Firefly insists on an end date after the start; a week ahead also
                # covers rows the broker dates a day or two forward.
                end = (datetime.date.today() + datetime.timedelta(days=7)).isoformat()
                params.update(start=since, end=end)
            d = api(f"/api/v1/accounts/{acct}/transactions", params=params)
            for g in d["data"]:
                for s in g["attributes"]["transactions"]:
                    if s.get("external_id"):
                        ids.add(s["external_id"])
            if page >= max(1, d["meta"]["pagination"]["total_pages"]):
                break
            page += 1
    return ids


def own_ibans():
    """IBANs of every asset and liability account in Firefly, i.e. your own accounts.

    Read from the ledger rather than configured, so a new bank account is covered as
    soon as its IBAN is entered there. Firefly holds one IBAN per account, so an
    account's previous IBAN - a bank merger changes it, while history keeps the old one -
    goes into its account number field, which is read too.
    """
    ibans = set()
    for kind in ("asset", "liabilities"):
        for a in api("/api/v1/accounts", params={"type": kind, "limit": 500})["data"]:
            for field_ in ("iban", "account_number"):
                v = re.sub(r"[^A-Z0-9]", "", (a["attributes"].get(field_) or "").upper())
                if re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", v):
                    ibans.add(v)
    return ibans


def read_stamp(state_dir, name):
    """Epoch seconds stored in state_dir/name, or None if missing or unreadable."""
    try:
        with open(os.path.join(state_dir, name), encoding="utf-8") as fh:
            return float(fh.read().strip())
    except (OSError, ValueError):
        return None


def write_stamp(state_dir, name, when):
    with open(os.path.join(state_dir, name), "w", encoding="utf-8") as fh:
        fh.write(f"{when}\n")


def reminder_due(now, last_import, last_reminder, remind_after_days):
    """Whether an expired session is worth a mail.

    A web session lives 24 hours from the login and refreshing does not extend it, so
    most runs find it expired and that is the normal state, not a fault. Only a long
    gap since the last successful import is worth mentioning, and at most once a day.
    """
    day = 86400
    if last_import is not None and now - last_import < remind_after_days * day:
        return False
    return last_reminder is None or now - last_reminder >= day


def session_alive():
    """Whether the saved Trade Republic web session can be resumed.

    Checked before pytr runs because pytr falls back to a fresh login when resuming
    fails, and a login sends a code to the phone and then waits for someone to type it
    in - at night, in a pod nobody is watching. This job only ever resumes; logging in is
    left to a person.
    """
    from pytr.api import TradeRepublicApi
    lines = (pathlib.Path.home() / ".pytr" / "credentials").read_text().split("\n")
    tr = TradeRepublicApi(phone_no=lines[0].strip(), pin=lines[1].strip(), save_cookies=True)
    return tr.resume_websession()


def fetch_events(workdir, days):
    subprocess.run(["pytr", "export_transactions", "--last_days", str(days),
                    "--export-format", "json", "--outputdir", workdir], check=True)
    with open(os.path.join(workdir, "all_events.json"), encoding="utf-8") as fh:
        return json.load(fh)


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


def report(subject, body):
    print(body)
    if os.environ.get("MAIL_HOST"):
        send_mail(subject, body)
    else:
        print("no MAIL_HOST configured - not mailing")


def main():
    cash = os.environ.get("TR_CASH_ACCOUNT", "257")
    depot = os.environ.get("TR_DEPOT_ACCOUNT", "258")
    asset_accounts = {k: str(v) for k, v in json.loads(os.environ.get("TR_ASSET_ACCOUNTS", "{}")).items()}
    name_alias = json.loads(os.environ.get("TR_NAME_ALIAS", "{}"))
    window = int(os.environ.get("TR_WINDOW_DAYS", "90"))
    remind_days = int(os.environ.get("TR_REMIND_AFTER_DAYS", "14"))
    state_dir = os.environ.get("TR_STATE_DIR", str(pathlib.Path.home() / ".pytr"))
    dry_run = os.environ.get("TR_DRY_RUN", "false").lower() == "true"
    # 0 looks at the whole history; a re-run of everything books only what is missing.
    since = ((datetime.date.today() - datetime.timedelta(days=window)).isoformat()
             if window else "")

    if not session_alive():
        last = read_stamp(state_dir, "last-import")
        if reminder_due(time.time(), last, read_stamp(state_dir, "last-reminder"), remind_days):
            since_txt = (datetime.date.fromtimestamp(last).isoformat() if last else "never")
            report("Firefly TR import: Trade Republic login needed",
                   f"No Trade Republic transactions have been imported since {since_txt}: "
                   "the saved session has expired. Log in once; the import runs right "
                   "after the login.\n\n" + os.environ.get("TR_LOGIN_HINT", ""))
            write_stamp(state_dir, "last-reminder", time.time())
        else:
            print("session expired; no reminder due")
        return 0
    events = fetch_events(os.environ.get("TR_WORKDIR", "/work"), window)

    accounts = {cash, depot, *asset_accounts.values()}
    posts, odd = plan_posts(events, existing_external_ids(accounts, since), since, cash, depot,
                            asset_accounts, name_alias, own_ibans())
    print(f"window {since or 'all'}: {len(events)} events, {len(posts)} new rows, "
          f"{len(odd)} of an unknown type")

    problems = [f"unknown event type {e.get('eventType')} on {e.get('timestamp', '')[:10]}: "
                f"{(e.get('amount') or {}).get('value')} {e.get('title', '')} - not booked"
                for e in odd]
    posted = 0
    for p in posts:
        if dry_run:
            print("would post", p["date"], p["type"], p["amount"], p["description"])
            continue
        try:
            api("/api/v1/transactions", "POST", {"error_if_duplicate_hash": True,
                                                 "apply_rules": True, "fire_webhooks": False,
                                                 "transactions": [p]})
            posted += 1
        except urllib.error.HTTPError as e:
            problems.append(f"{p['external_id']} {p['date']} {p['amount']} {p['description']}: "
                            f"HTTP {e.code} {e.read()[:200].decode(errors='replace')}")
    print(f"posted {posted}")
    if not dry_run:
        write_stamp(state_dir, "last-import", time.time())
    if problems:
        report(f"Firefly TR import: {len(problems)} needing attention",
               "".join(f"  - {l}\n" for l in problems))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
