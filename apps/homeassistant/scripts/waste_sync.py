import base64, datetime, html.parser, os, re, sys, urllib.error, urllib.request
import xml.etree.ElementTree as ET

# Copies the waste collection dates of one address from a mein-abfallkalender.online
# results page into a CalDAV collection, where Home Assistant and phones read them.
#
# The provider's own iCal feed only works for an account registered with e-mail
# reminders, so this reads the public results page instead - the same page any
# visitor sees. That page is HTML meant for people: when its layout changes, parsing
# finds nothing, and check_plausible() turns that into a failed run instead of an
# emptied calendar.
#
# Environment:
#   SOURCE_URL       results page URL including street and waste type parameters. It
#                    identifies the address, so it comes from a secret, never from git.
#   CALDAV_URL       collection URL, created on the first run if missing
#   CALDAV_USERNAME, CALDAV_PASSWORD
#   USER_AGENT       sent to the provider, so they can reach someone if it bothers them

DATE_CELL = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")
HREF_PREFIX = "waste-"
HREF_DATE = re.compile(r"^waste-(\d{8})-")
TIMEOUT_S = 30


class PlausibilityError(Exception):
    pass


class _ResultsTable(html.parser.HTMLParser):
    """Collects (date text, link texts) per table row of the results page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self._cells = None
        self._links = None
        self._cell = None
        self._link = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._cells, self._links = [], []
        elif tag == "td" and self._cells is not None:
            self._cell = []
        elif tag == "a" and self._cells is not None:
            self._link = []

    def handle_endtag(self, tag):
        if tag == "a" and self._link is not None:
            self._links.append("".join(self._link).strip())
            self._link = None
        elif tag == "td" and self._cell is not None:
            self._cells.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._cells is not None:
            self.rows.append((self._cells, self._links))
            self._cells = self._links = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        if self._link is not None:
            self._link.append(data)


def parse_pickups(page):
    """(date, waste type) for every pickup row; header and month rows have no date cell.

    The waste type is the text of the row's link to the type's description, so the
    names are the provider's own (German) ones and appear unchanged in the calendar.
    """
    table = _ResultsTable()
    table.feed(page)
    pickups = []
    for cells, links in table.rows:
        dates = [m for m in (DATE_CELL.match(c) for c in cells) if m]
        if not dates or not links or not links[0]:
            continue
        day, month, year = (int(g) for g in dates[0].groups())
        pickups.append((datetime.date(year, month, day), links[0]))
    return pickups


def _slug(text):
    for umlaut, ascii_form in (("\u00e4", "ae"), ("\u00f6", "oe"), ("\u00fc", "ue"), ("\u00df", "ss")):
        text = text.lower().replace(umlaut, ascii_form)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def event_href(date, waste_type):
    return f"{HREF_PREFIX}{date:%Y%m%d}-{_slug(waste_type)}.ics"


def _escape(text):
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def event_ics(date, waste_type):
    """One all-day event. Built from date and type alone - no timestamp of the run -
    because plan_sync() never rewrites an href that already exists: anything that
    could differ between two runs would never reach the calendar."""
    uid = event_href(date, waste_type)[:-len(".ics")]
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//homelab//waste-sync//EN",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{date:%Y%m%d}T000000Z",
        f"DTSTART;VALUE=DATE:{date:%Y%m%d}",
        f"DTEND;VALUE=DATE:{date + datetime.timedelta(days=1):%Y%m%d}",
        f"SUMMARY:{_escape(waste_type)}",
        "TRANSP:TRANSPARENT",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n"


def hrefs_from_multistatus(body):
    """Item names (last path segment) listed in a PROPFIND Depth: 1 answer."""
    names = set()
    for href in ET.fromstring(body).iter("{DAV:}href"):
        path = (href.text or "").strip()
        if path and not path.endswith("/"):
            names.add(path.rsplit("/", 1)[-1])
    return names


def _href_date(href):
    m = HREF_DATE.match(href)
    return datetime.datetime.strptime(m.group(1), "%Y%m%d").date() if m else None


def plan_sync(pickups, existing, today):
    """Returns ({href: ics} to create, [href] to delete).

    Only future items this job created are ever deleted: past pickups stay as a record,
    and anything added to the collection by hand does not follow the naming.
    """
    wanted = {event_href(d, w): (d, w) for d, w in pickups if d >= today}
    puts = {href: event_ics(*wanted[href]) for href in sorted(wanted) if href not in existing}
    deletes = sorted(h for h in existing
                     if h not in wanted and (_href_date(h) or datetime.date.min) >= today)
    return puts, deletes


def check_plausible(new_future, existing_future):
    """Refuses results that look like a broken page rather than a changed schedule.

    Zero upcoming pickups is never a real schedule. Losing more than half of the
    upcoming ones at once is far more likely a layout change than the provider
    cancelling them; the ratio is not applied to tiny calendars, where a single
    dropped date already crosses it.
    """
    if new_future == 0:
        raise PlausibilityError("the results page lists no upcoming pickups")
    if existing_future >= 4 and new_future * 2 < existing_future:
        raise PlausibilityError(
            f"only {new_future} upcoming pickups found, the calendar has {existing_future}")


class CalDav:
    def __init__(self, collection_url, username, password):
        self.url = collection_url.rstrip("/") + "/"
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        self.auth = f"Basic {token}"

    def _request(self, method, url, body=None, headers=None):
        req = urllib.request.Request(url, data=body.encode() if body else None, method=method)
        req.add_header("Authorization", self.auth)
        for name, value in (headers or {}).items():
            req.add_header(name, value)
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return resp.read().decode()

    def list_items(self):
        """Item names in the collection; creates the collection first if it is missing."""
        propfind = '<?xml version="1.0"?><propfind xmlns="DAV:"><prop><resourcetype/></prop></propfind>'
        try:
            body = self._request("PROPFIND", self.url, propfind,
                                 {"Depth": "1", "Content-Type": "application/xml"})
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            self._request("MKCALENDAR", self.url)
            return set()
        return hrefs_from_multistatus(body)

    def put(self, href, ics):
        self._request("PUT", self.url + href, ics, {"Content-Type": "text/calendar; charset=utf-8"})

    def delete(self, href):
        self._request("DELETE", self.url + href)


def fetch(url, user_agent):
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return resp.read().decode("utf-8")


def main():
    env = os.environ
    today = datetime.date.today()
    pickups = parse_pickups(fetch(env["SOURCE_URL"], env.get("USER_AGENT", "waste-sync")))
    dav = CalDav(env["CALDAV_URL"], env["CALDAV_USERNAME"], env["CALDAV_PASSWORD"])
    existing = dav.list_items()

    new_future = sum(1 for d, _ in pickups if d >= today)
    existing_future = sum(1 for h in existing if (_href_date(h) or datetime.date.min) >= today)
    try:
        check_plausible(new_future, existing_future)
    except PlausibilityError as e:
        print(f"refusing to sync, calendar left unchanged: {e}", file=sys.stderr)
        return 1

    puts, deletes = plan_sync(pickups, existing, today)
    for href, ics in puts.items():
        dav.put(href, ics)
        print(f"added {href}")
    for href in deletes:
        dav.delete(href)
        print(f"removed {href}")
    last = max(d for d, _ in pickups)
    print(f"{new_future} upcoming pickups until {last:%Y-%m-%d}: "
          f"{len(puts)} added, {len(deletes)} removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
