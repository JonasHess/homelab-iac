import pytest
from conftest import _load

ls = _load("loan_split")

LOANS = {"50":  {"label": "Objekt A",  "payee": 50,  "liability": 987,  "interest": 994},
         "49":  {"label": "Objekt B",  "payee": 49,  "liability": 990,  "interest": 995},
         "244": {"label": "Objekt C",  "payee": 244, "liability": 1000, "interest": 1003}}

# The two wordings lenders use, which differ in the order of the two parts.
FMT_A  = "Teilzahlung Darlehen       RECHN.ZINS         312,34  TILG./ENTG.        187,66  TILGUNG PER    01.10.2026"
FMT_B  = "Rechnung Darl.-Leistung 123456789 Tilgung 140,25 Zinsen 159,75"
BIG  = "Teilzahlung Darlehen       RECHN.ZINS       1.234,56  TILG./ENTG.      2.000,00"


def group(gid, dest, amt, desc, src="237", legs=1, ext="e1", cat="Immobilie/Hausgeld"):
    split = {"transaction_journal_id": "j", "type": "withdrawal",
             "date": "2026-11-05T00:00:00+01:00", "amount": amt,
             "source_id": src, "destination_id": dest, "description": desc,
             "category_name": cat, "external_id": ext}
    return {"id": gid, "attributes": {"transactions": [split] * legs}}


class TestAmounts:
    def test_german_format(self):
        assert ls.cents_de("312,34") == 31234
        assert ls.cents_de("1.234,56") == 123456
        assert ls.cents_de("2.000,00") == 200000

    def test_api_format_and_round_trip(self):
        assert ls.cents("500.00") == 50000
        assert ls.amount(50000) == "500.00"
        assert ls.amount(18766) == "187.66"
        assert ls.amount(5) == "0.05"


class TestBreakdown:
    def test_zins_first_wording(self):
        assert ls.breakdown(FMT_A) == (31234, 18766)

    def test_tilgung_first_wording(self):
        # One lender names Tilgung first; returning it unswapped would put
        # the interest on the debt and quietly understate the loan.
        assert ls.breakdown(FMT_B) == (15975, 14025)

    def test_thousands_separator(self):
        assert ls.breakdown(BIG) == (123456, 200000)

    @pytest.mark.parametrize("desc", ["", None, "WEG 0481 Wohngeld 10 26 WNr 55",
                                      "Darlehensrate ohne Aufschluesselung"])
    def test_no_match_is_none(self, desc):
        assert ls.breakdown(desc) is None


class TestPlan:
    def test_actionable_payment(self):
        actions, problems = ls.plan([group("1", "50", "500.00", FMT_A)], LOANS)
        assert problems == []
        assert len(actions) == 1
        a = actions[0]
        assert (a["zins"], a["tilgung"], a["total"]) == (31234, 18766, 50000)
        assert a["zins"] + a["tilgung"] == a["total"]

    def test_legs_must_sum_to_the_lump(self):
        # The single guard that stops a misparse from moving money.
        actions, problems = ls.plan([group("2", "49", "600.00", FMT_A)], LOANS)
        assert actions == []
        assert "!= lump" in problems[0]

    def test_unknown_wording_is_reported_not_guessed(self):
        actions, problems = ls.plan(
            [group("3", "244", "350.00", "Darlehensrate ohne Aufschluesselung")], LOANS)
        assert actions == []
        assert "no known Zins/Tilgung wording" in problems[0]

    def test_already_split_is_ignored(self):
        actions, problems = ls.plan([group("4", "50", "187.66", FMT_A, legs=2)], LOANS)
        assert (actions, problems) == ([], [])

    def test_other_payees_are_ignored(self):
        actions, problems = ls.plan(
            [group("5", "51", "280.00", "XC00000000, 1, 11/26 Hausgeld")], LOANS)
        assert (actions, problems) == ([], [])

    def test_duplicate_group_id_handled_once(self):
        # The paginated listing repeats a group sitting on a page boundary; acting
        # twice would fail the second time, after the first had already rewritten it.
        g = group("6", "50", "500.00", FMT_A)
        actions, problems = ls.plan([g, g], LOANS)
        assert len(actions) == 1
        assert problems == []

    def test_no_loans_configured_does_nothing(self):
        actions, problems = ls.plan([group("7", "50", "500.00", FMT_A)], {})
        assert (actions, problems) == ([], [])


class TestSplitPayload:
    def setup_method(self):
        actions, _ = ls.plan([group("8", "50", "500.00", FMT_A, ext="ext-1")], LOANS)
        self.p = ls.split_payload(actions[0])

    def test_two_legs_summing_to_the_lump(self):
        legs = self.p["transactions"]
        assert len(legs) == 2
        assert ls.cents(legs[0]["amount"]) + ls.cents(legs[1]["amount"]) == 50000

    def test_tilgung_goes_to_the_liability_and_zins_to_the_expense(self):
        tilg, zins = self.p["transactions"]
        assert (tilg["amount"], tilg["destination_id"]) == ("187.66", "987")
        assert (zins["amount"], zins["destination_id"]) == ("312.34", "994")

    def test_external_id_rides_on_the_tilgung_leg_only(self):
        # Dropping it would make the importer re-add the bank's lump while the
        # row is still inside its re-fetch window.
        tilg, zins = self.p["transactions"]
        assert tilg["external_id"] == "ext-1"
        assert "external_id" not in zins

    def test_group_title_is_required_by_the_api(self):
        assert self.p["group_title"] == "Teilzahlung Objekt A 05.11.2026"


class TestLabels:
    def test_a_configured_label_is_kept(self):
        loans = [{"label": "Objekt A", "payee": 50, "liability": 987, "interest": 994}]
        assert ls.label_loans(loans, {"987": "Darlehen X"})[0]["label"] == "Objekt A"

    def test_without_a_label_the_liability_name_is_used(self):
        # So the configuration needs no name for the property or the lender.
        loans = [{"payee": 50, "liability": 987, "interest": 994}]
        assert ls.label_loans(loans, {"987": "Darlehen X"})[0]["label"] == "Darlehen X"
