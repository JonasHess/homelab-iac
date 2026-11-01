import datetime
import pathlib
import sys
import unittest

# The script ships in the chart (ConfigMap), not as a package: import it by path.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "apps" / "homeassistant" / "scripts"))

from waste_sync import (
    PlausibilityError,
    check_plausible,
    event_href,
    event_ics,
    hrefs_from_multistatus,
    parse_pickups,
    plan_sync,
)

# Shaped like the provider's results table; street, dates and waste types are invented.
RESULTS_PAGE = """
<html><body>
<div id="results">
<table>
  <caption>Ihre individuellen Abfalltermine im gew&auml;hlten Zeitraum: 3 zuk&uuml;nftige Termine</caption>
  <tr><th>Datum</th><th colspan="2">Abfallart</th><th>Tag</th><th id="last_column" class="no_print"></th></tr>
  <tr><th scope="col" colspan="5"><h3>M&auml;rz 2031 - Abfalltermine f&uuml;r "Musterweg HsNr. 1-9, Musterstadt"</h3></th></tr>
  <tr>
    <td>03.03.2031</td>
    <td class="waste_type"><div class="Bio"></div></td>
    <td class="nowrap"><a href="abfallarten#Bio" title="Zur Beschreibung der Abfallart Biom&uuml;ll">Biom&uuml;ll</a></td>
    <td>Montag</td><td class="no_print"> </td>
  </tr>
  <tr>
    <td>11.03.2031</td>
    <td class="waste_type"><div class="HM-2"></div></td>
    <td class="nowrap"><a href="abfallarten#HM-2" title="Zur Beschreibung der Abfallart Hausm&uuml;ll 14 t&auml;gig">Hausm&uuml;ll 14 t&auml;gig</a></td>
    <td>Dienstag</td><td class="no_print"> </td>
  </tr>
  <tr><th scope="col" colspan="5"><h3>April 2031 - Abfalltermine f&uuml;r "Musterweg HsNr. 1-9, Musterstadt"</h3></th></tr>
  <tr>
    <td>02.04.2031</td>
    <td class="waste_type"><div class="GS"></div></td>
    <td class="nowrap"><a href="abfallarten#GS" title="Zur Beschreibung der Abfallart Gelber Sack">Gelber Sack</a></td>
    <td>Mittwoch</td><td class="no_print"> </td>
  </tr>
</table>
</div>
<p>Stand: 01.12.2030</p>
</body></html>
"""

MULTISTATUS = """<?xml version="1.0" encoding="utf-8"?>
<multistatus xmlns="DAV:">
  <response><href>/waste-sync/waste-collection/</href><propstat><status>HTTP/1.1 200 OK</status></propstat></response>
  <response><href>/waste-sync/waste-collection/waste-20310303-biomuell.ics</href><propstat><status>HTTP/1.1 200 OK</status></propstat></response>
  <response><href>/waste-sync/waste-collection/added-by-hand.ics</href><propstat><status>HTTP/1.1 200 OK</status></propstat></response>
</multistatus>
"""

D = datetime.date


class ParsePickupsTest(unittest.TestCase):
    def test_reads_every_pickup_row(self):
        self.assertEqual(parse_pickups(RESULTS_PAGE), [
            (D(2031, 3, 3), "Biomüll"),
            (D(2031, 3, 11), "Hausmüll 14 tägig"),
            (D(2031, 4, 2), "Gelber Sack"),
        ])

    def test_page_without_table_has_no_pickups(self):
        self.assertEqual(parse_pickups("<html><body>Wartungsarbeiten</body></html>"), [])

    def test_row_without_waste_type_is_skipped(self):
        page = "<table><tr><td>03.03.2031</td><td>Montag</td></tr></table>"
        self.assertEqual(parse_pickups(page), [])


class EventTest(unittest.TestCase):
    def test_href_is_ascii_and_stable(self):
        self.assertEqual(event_href(D(2031, 3, 11), "Hausmüll 14 tägig"),
                         "waste-20310311-hausmuell-14-taegig.ics")

    def test_ics_is_an_all_day_event_without_alarm(self):
        ics = event_ics(D(2031, 3, 11), "Hausmüll 14 tägig")
        self.assertIn("\r\n", ics)
        self.assertNotIn("\n", ics.replace("\r\n", ""))
        self.assertIn("UID:waste-20310311-hausmuell-14-taegig\r\n", ics)
        self.assertIn("DTSTART;VALUE=DATE:20310311\r\n", ics)
        self.assertIn("DTEND;VALUE=DATE:20310312\r\n", ics)
        self.assertIn("SUMMARY:Hausmüll 14 tägig\r\n", ics)
        self.assertNotIn("VALARM", ics)

    def test_ics_depends_only_on_date_and_type(self):
        # An existing href is skipped on later runs, which is only right if the same
        # date and type always produce the same event.
        self.assertEqual(event_ics(D(2031, 3, 3), "Biomüll"), event_ics(D(2031, 3, 3), "Biomüll"))

    def test_summary_text_is_escaped(self):
        self.assertIn("SUMMARY:Papier\\, Pappe\\; Kartonagen\r\n",
                      event_ics(D(2031, 3, 3), "Papier, Pappe; Kartonagen"))


class MultistatusTest(unittest.TestCase):
    def test_lists_items_but_not_the_collection_itself(self):
        self.assertEqual(hrefs_from_multistatus(MULTISTATUS),
                         {"waste-20310303-biomuell.ics", "added-by-hand.ics"})


class PlanSyncTest(unittest.TestCase):
    today = D(2031, 3, 5)

    def test_adds_new_pickups_and_keeps_existing_ones(self):
        puts, deletes = plan_sync(
            [(D(2031, 3, 11), "Hausmüll 14 tägig"), (D(2031, 4, 2), "Gelber Sack")],
            {"waste-20310311-hausmuell-14-taegig.ics"},
            self.today)
        self.assertEqual(list(puts), ["waste-20310402-gelber-sack.ics"])
        self.assertEqual(deletes, [])

    def test_deletes_future_pickups_the_provider_dropped(self):
        _, deletes = plan_sync(
            [(D(2031, 4, 2), "Gelber Sack")],
            {"waste-20310402-gelber-sack.ics", "waste-20310311-hausmuell-14-taegig.ics"},
            self.today)
        self.assertEqual(deletes, ["waste-20310311-hausmuell-14-taegig.ics"])

    def test_keeps_past_pickups_and_foreign_items(self):
        _, deletes = plan_sync(
            [(D(2031, 4, 2), "Gelber Sack")],
            {"waste-20310402-gelber-sack.ics", "waste-20310303-biomuell.ics", "added-by-hand.ics"},
            self.today)
        self.assertEqual(deletes, [])

    def test_ignores_pickups_before_today(self):
        puts, _ = plan_sync([(D(2031, 3, 3), "Biomüll")], set(), self.today)
        self.assertEqual(dict(puts), {})


class PlausibilityTest(unittest.TestCase):
    def test_no_pickups_at_all_is_refused(self):
        with self.assertRaises(PlausibilityError):
            check_plausible(new_future=0, existing_future=0)

    def test_losing_more_than_half_is_refused(self):
        with self.assertRaises(PlausibilityError):
            check_plausible(new_future=4, existing_future=10)

    def test_normal_run_passes(self):
        check_plausible(new_future=9, existing_future=10)

    def test_small_calendars_are_not_judged_by_ratio(self):
        check_plausible(new_future=1, existing_future=3)


if __name__ == "__main__":
    unittest.main()
