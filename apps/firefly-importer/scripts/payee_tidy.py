import datetime, json, os, re, sys, urllib.error, urllib.request

# Keeps one payee per merchant on withdrawals. Two things split a merchant otherwise:
#
# - A PayPal payment reaches Firefly as a withdrawal to "PayPal Europe ...", which hides
#   where the money actually went. The bank's description names the merchant, so that
#   merchant becomes the payee. Payments to private people carry no merchant - PayPal
#   leaves the name out - and stay with PayPal.
# - Firefly matches payee names exactly, and banks spell one merchant differently (one
#   upper-cases card payments, another does not), so each spelling opens its own payee.
#   Payments on a second spelling are moved onto the most readable one.
#
# Only the payee changes. The update runs without the rule engine, so categories a person
# or a rule has set stay as they are. Emptied payee accounts are left in place: removing
# accounts is not a job for an unattended run, and an empty one does no harm.

PAYPAL = re.compile(r"(?i)paypal")
MARK = "\x1f"  # stands for a bank line break ("; ") while a description is parsed
LINE = 35  # width of a SEPA remittance line, at which banks cut the description
# Umlauts appear as themselves, transcribed (ae) or dropped (a); all three fold to the plain
# vowel, as in the ledger check, so both jobs agree on what one payee is.
UMLAUTS = str.maketrans({"\u00e4": "a", "\u00f6": "o", "\u00fc": "u", "\u00df": "ss"})


def _phrase(text):
    """Regex for `text` that tolerates a line break between any two characters."""
    return (MARK + "?").join(re.escape(c) for c in text)


IHR = _phrase("Ihr Einkauf bei ")
FIRST = re.compile(r"\d{10,}/(?:PP\.\d+\.PP/)?\.\s*(.*?),\s*" + _phrase("Ihr"), re.I)
SECOND = re.compile(IHR + r"(.*?)(?:" + MARK + r"?\s?Lastschrift|\sEREF:|\sSVWZ\+|$)", re.I)
CARD = re.compile(r"PAYPAL \*(\S+)")


def _letters(segment):
    """(letters without spaces, gaps followed by a space, gaps at an unresolved break)."""
    letters, spaces, breaks = [], set(), set()
    for ch in segment.strip(MARK + " "):
        if ch == " ":
            if letters:
                spaces.add(len(letters) - 1)
        elif ch == MARK:
            if letters:
                breaks.add(len(letters) - 1)
        else:
            letters.append(ch)
    return letters, spaces, breaks


def _unwrap(description):
    """Undo a cut into LINE-wide lines joined with single spaces, if that is what happened.

    Such a join puts a space at every LINE+1-th position. When all of them are spaces the
    description is taken to be joined that way and they are removed, which restores split
    words exactly. A description that merely happens to have spaces there would lose them;
    that only respaces a name, it never picks a different merchant.
    """
    cuts = range(LINE, len(description), LINE + 1)
    if not cuts or any(description[i] != " " for i in cuts):
        return description
    return "".join(description[i:i + LINE] for i in range(0, len(description), LINE + 1))


def merchant(description):
    """The merchant a PayPal payment went to, or None when the description names none.

    Banks wrap the description at a fixed width, and the merchant appears at least twice
    in it, wrapped at different places. Two habits need opposite repairs:

    - joining lines with "; " drops a space that falls on the break and splits words that
      straddle it; real spaces are exact, so a break takes whatever another copy shows at
      that place. Where every copy breaks at the same place, a capital letter after a
      lower-case one is read as the start of a new word - a guess, but one that only
      decides how a name is spaced, never which merchant it is;
    - joining lines with a space inserts spaces that were never there; they sit at fixed
      positions and are removed first, and of what is left a space is real only if every
      copy has it.
    """
    m = CARD.search(description)
    if m:
        return m.group(1)
    joined_with_marks = "; " in description
    text = description.replace("; ", MARK) if joined_with_marks else _unwrap(description)
    first = FIRST.search(text)
    if not first or not first.group(1).replace(MARK, "").strip():
        return None
    letters, spaces, breaks = _letters(first.group(1))
    copies = [_letters(c.group(1)) for c in FIRST.finditer(text)]
    copies += [_letters(c.group(1)) for c in SECOND.finditer(text)]
    copies = [c for c in copies if c[0] == letters]
    if joined_with_marks:
        for gap in breaks:
            if any(gap in c[1] for c in copies):
                spaces.add(gap)
            elif all(gap in c[2] for c in copies) and gap + 1 < len(letters) \
                    and letters[gap].islower() and letters[gap + 1].isupper():
                spaces.add(gap)
    else:
        for _, other_spaces, _ in copies:
            spaces &= other_spaces
    out = []
    for i, ch in enumerate(letters):
        out.append(ch)
        if i in spaces:
            out.append(" ")
    return "".join(out).strip() or None


def same_payee_key(name):
    """Names that differ only in spacing, punctuation or case belong to one payee.

    Descriptions spell a merchant inconsistently - one bank upper-cases everything, older
    exports drop every space - so matching on the exact name would open a new payee for
    each spelling and split one merchant's spending across several.
    """
    key = re.sub(r"[^a-z0-9]", "", name.lower().translate(UMLAUTS))
    return key.replace("ae", "a").replace("oe", "o").replace("ue", "u")


def preferred(payees):
    """Id of the most readable of several spellings: spaced, then mixed case, then oldest."""
    def score(item):
        pid, name = item
        mixed = any(c.islower() for c in name) and name[:1].isupper()
        return (name.count(" "), mixed, -int(pid))
    return max(payees.items(), key=score)[0]


def plan(groups, payees=None, ignore_ids=()):
    """Payee changes for single-split withdrawals.

    payees     - existing expense accounts, id -> name. A merchant that already has one,
                 in whatever spelling, is booked onto it rather than onto a new account.
    ignore_ids - payee ids kept apart on purpose although their names fold together
                 (someone holding two accounts at one bank); they are never merged.
    """
    skip = {str(i) for i in ignore_ids}
    by_key = {}
    for pid, name in (payees or {}).items():
        if str(pid) not in skip:
            by_key.setdefault(same_payee_key(name), {})[pid] = name
    changes, seen = [], set()
    for g in groups:
        txs = g["attributes"]["transactions"]
        if g["id"] in seen or len(txs) != 1:
            continue
        seen.add(g["id"])
        s = txs[0]
        old = s.get("destination_name") or ""
        if s["type"] != "withdrawal" or str(s.get("destination_id")) in skip:
            continue
        name = merchant(s.get("description") or "") if PAYPAL.search(old) else old
        if not name:
            continue
        change = {"group": g["id"], "journal": s["transaction_journal_id"],
                  "date": s["date"][:10], "amount": s["amount"], "old": old, "new": name}
        known = by_key.get(same_payee_key(name))
        if known:
            change["payee_id"] = preferred(known)
            change["new"] = known[change["payee_id"]]
            if change["payee_id"] == str(s.get("destination_id")):
                continue
        if change["new"] != old:
            changes.append(change)
    return changes


def payload(change):
    target = ({"destination_id": change["payee_id"]} if "payee_id" in change
              else {"destination_name": change["new"]})
    return {"apply_rules": False, "fire_webhooks": False,
            "transactions": [{"transaction_journal_id": change["journal"], **target}]}


def api(path, method="GET", body=None):
    base = os.environ["FIREFLY_III_URL"].rstrip("/")
    req = urllib.request.Request(base + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {os.environ['FIREFLY_III_ACCESS_TOKEN']}",
                                          "Accept": "application/json", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def main():
    window = int(os.environ.get("PAYEE_TIDY_WINDOW_DAYS", "90"))
    dry_run = os.environ.get("PAYEE_TIDY_DRY_RUN", "false").lower() == "true"
    ignore_ids = json.loads(os.environ.get("PAYEE_TIDY_IGNORE_IDS") or "[]")
    today = datetime.date.today()
    since = (today - datetime.timedelta(days=window)).isoformat()
    groups, page = [], 1
    while True:
        d = api(f"/api/v1/transactions?type=withdrawal&start={since}&end={today.isoformat()}"
                f"&limit=500&page={page}")
        groups.extend(d["data"])
        if page >= max(1, d["meta"]["pagination"]["total_pages"]):
            break
        page += 1
    payees, page = {}, 1
    while True:
        d = api(f"/api/v1/accounts?type=expense&limit=500&page={page}")
        payees.update({a["id"]: a["attributes"]["name"] for a in d["data"]})
        if page >= max(1, d["meta"]["pagination"]["total_pages"]):
            break
        page += 1
    changes = plan(groups, payees, ignore_ids)
    print(f"window {since} .. {today}: {len(groups)} withdrawals, {len(changes)} payees to change")
    failed = 0
    for c in changes:
        label = f"g{c['group']} {c['date']} {float(c['amount']):.2f}: {c['old']} -> {c['new']}"
        if dry_run:
            print("would set", label)
            continue
        try:
            api(f"/api/v1/transactions/{c['group']}", "PUT", payload(c))
            print("set", label)
        except urllib.error.HTTPError as e:
            failed += 1
            print(f"FAILED {label}: HTTP {e.code} {e.read()[:200]!r}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
