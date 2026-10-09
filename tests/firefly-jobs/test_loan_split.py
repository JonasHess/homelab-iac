import pytest
from conftest import _load

ls = _load("loan_split")

LOANS = {"50":  {"label": "Wormser",    "payee": 50,  "liability": 987,  "interest": 994},
         "49":  {"label": "Berliner",   "payee": 49,  "liability": 990,  "interest": 995},
         "244": {"label": "Chrodegang", "payee": 244, "liability": 1000, "interest": 1003}}

# Real wordings taken from the bank, including the two different orderings.
PSD  = "Teilzahlung Darlehen       RECHN.ZINS         362,77  TILG./ENTG.        206,23  TILGUNG PER    01.10.2026"
SPK  = "Rechnung Darl.-Leistung 620409417 Tilgung 173,04 Zinsen 176,96"
BIG  = "Teilzahlung Darlehen       RECHN.ZINS       1.234,56  TILG./ENTG.      2.000,00"


def group(gid, dest, amt, desc, src="237", legs=1, ext="e1", cat="Immobilie/Hausgeld"):
    split = {"transaction_journal_id": "j", "type": "withdrawal",
             "date": "2026-11-05T00:00:00+01:00", "amount": amt,
             "source_id": src, "destination_id": dest, "description": desc,
             "category_name": cat, "external_id": ext}
    return {"id": gid, "attributes": {"transactions": [split] * legs}}


class TestAmounts:
    def test_german_format(self):
        assert ls.cents_de("362,77") == 36277
        assert ls.cents_de("1.234,56") == 123456
        assert ls.cents_de("2.000,00") == 200000

    def test_api_format_and_round_trip(self):
        assert ls.cents("569.00") == 56900
        assert ls.amount(56900) == "569.00"
        assert ls.amount(20623) == "206.23"
        assert ls.amount(5) == "0.05"


class TestBreakdown:
    def test_zins_first_wording(self):
        assert ls.breakdown(PSD) == (36277, 20623)

    def test_tilgung_first_wording(self):
        # The Sparkasse names Tilgung first; returning it unswapped would put
        # the interest on the debt and quietly understate the loan.
        assert ls.breakdown(SPK) == (17696, 17304)

    def test_thousands_separator(self):
        assert ls.breakdown(BIG) == (123456, 200000)

    @pytest.mark.parametrize("desc", ["", None, "WEG 0481 Wohngeld 10 26 WNr 55",
                                      "Darlehensrate ohne Aufschluesselung"])
    def test_no_match_is_none(self, desc):
        assert ls.breakdown(desc) is None


class TestPlan:
    def test_actionable_payment(self):
        actions, problems = ls.plan([group("1", "50", "569.00", PSD)], LOANS)
        assert problems == []
        assert len(actions) == 1
        a = actions[0]
        assert (a["zins"], a["tilgung"], a["total"]) == (36277, 20623, 56900)
        assert a["zins"] + a["tilgung"] == a["total"]

    def test_legs_must_sum_to_the_lump(self):
        # The single guard that stops a misparse from moving money.
        actions, problems = ls.plan([group("2", "49", "600.00", PSD)], LOANS)
        assert actions == []
        assert "!= lump" in problems[0]

    def test_unknown_wording_is_reported_not_guessed(self):
        actions, problems = ls.plan(
            [group("3", "244", "350.00", "Darlehensrate ohne Aufschluesselung")], LOANS)
        assert actions == []
        assert "no known Zins/Tilgung wording" in problems[0]

    def test_already_split_is_ignored(self):
        actions, problems = ls.plan([group("4", "50", "206.23", PSD, legs=2)], LOANS)
        assert (actions, problems) == ([], [])

    def test_other_payees_are_ignored(self):
        actions, problems = ls.plan(
            [group("5", "51", "280.00", "XC36473154, 40, 11/26 Hausgeld")], LOANS)
        assert (actions, problems) == ([], [])

    def test_duplicate_group_id_handled_once(self):
        # The paginated listing repeats a group sitting on a page boundary; acting
        # twice would fail the second time, after the first had already rewritten it.
        g = group("6", "50", "569.00", PSD)
        actions, problems = ls.plan([g, g], LOANS)
        assert len(actions) == 1
        assert problems == []

    def test_no_loans_configured_does_nothing(self):
        actions, problems = ls.plan([group("7", "50", "569.00", PSD)], {})
        assert (actions, problems) == ([], [])


class TestSplitPayload:
    def setup_method(self):
        actions, _ = ls.plan([group("8", "50", "569.00", PSD, ext="782855736")], LOANS)
        self.p = ls.split_payload(actions[0])

    def test_two_legs_summing_to_the_lump(self):
        legs = self.p["transactions"]
        assert len(legs) == 2
        assert ls.cents(legs[0]["amount"]) + ls.cents(legs[1]["amount"]) == 56900

    def test_tilgung_goes_to_the_liability_and_zins_to_the_expense(self):
        tilg, zins = self.p["transactions"]
        assert (tilg["amount"], tilg["destination_id"]) == ("206.23", "987")
        assert (zins["amount"], zins["destination_id"]) == ("362.77", "994")

    def test_external_id_rides_on_the_tilgung_leg_only(self):
        # Dropping it would make the importer re-add the bank's lump while the
        # row is still inside its re-fetch window.
        tilg, zins = self.p["transactions"]
        assert tilg["external_id"] == "782855736"
        assert "external_id" not in zins

    def test_group_title_is_required_by_the_api(self):
        assert self.p["group_title"] == "Teilzahlung Darlehen Wormser 05.11.2026"
