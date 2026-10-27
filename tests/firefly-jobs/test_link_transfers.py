import pytest
from conftest import _load

lt = _load("link_transfers")

# The real shape of the case this job exists for: a standing monthly transfer that both
# banks report, with the paying bank naming the payee after the receiving IBAN.
BBBANK = "DE31660908000252693119"
COMDIR = "DE13200411440494963200"
OWN = {BBBANK: {"id": "2", "name": "BBBank Giro"},
       COMDIR: {"id": "99", "name": "Comdirect Haushalt"}}

SELF_PAYEE = f"Michael Zimmermann ({COMDIR})"


def group(gid, split_list):
    return {"id": gid, "attributes": {"transactions": split_list}}


def wd(amount="1350.00", date="2026-10-26", src="2", payee=SELF_PAYEE,
       journal="7354", ext="2026-10-26.0"):
    return {"transaction_journal_id": journal, "type": "withdrawal", "date": date + "T00:00:00+01:00",
            "amount": amount, "source_id": src, "source_name": "BBBank Giro",
            "destination_id": "601", "destination_name": payee,
            "description": "Haushalt / DA-3", "external_id": ext}


def dp(amount="1350.00", date="2026-10-26", dest="99", journal="7356",
       ext="413A8C41CEB0443C8ADECAA6423F8945"):
    return {"transaction_journal_id": journal, "type": "deposit", "date": date + "T00:00:00+01:00",
            "amount": amount, "source_id": "27", "source_name": "Michael Zimmermann",
            "destination_id": dest, "destination_name": "Comdirect Haushalt",
            "description": "Haushalt Uebertrag / Ueberweisung", "external_id": ext}


def canonical():
    return [group("7268", [wd()]), group("7270", [dp()])]


class TestAmounts:
    def test_cents_round_trip(self):
        assert lt.cents("1350.00") == 135000
        assert lt.cents("1350.000000000000") == 135000
        assert lt.amount(135000) == "1,350.00"

    def test_cents_does_not_drift_on_awkward_values(self):
        assert lt.cents("110.85") == 11085
        assert lt.cents("0.07") == 7


class TestIbanInPayeeName:
    def test_embedded_iban_is_found(self):
        assert lt.own_account_in(SELF_PAYEE, OWN)["id"] == "99"

    def test_spacing_and_punctuation_are_ignored(self):
        spaced = "Michael Zimmermann DE13 2004 1144 0494 9632 00"
        assert lt.own_account_in(spaced, OWN)["id"] == "99"

    def test_lowercase_is_found(self):
        assert lt.own_account_in(f"zahlung an {COMDIR.lower()}", OWN)["id"] == "99"

    def test_a_foreign_iban_is_not_a_match(self):
        assert lt.own_account_in("REWE DE89370400440532013000", OWN) is None

    def test_no_name_is_not_a_match(self):
        assert lt.own_account_in(None, OWN) is None
        assert lt.own_account_in("", OWN) is None


class TestPairing:
    def test_the_canonical_pair(self):
        pairs, refusals = lt.plan(canonical(), OWN)
        assert refusals == []
        assert len(pairs) == 1
        p = pairs[0]
        assert (p["date"], p["cents"]) == ("2026-10-26", 135000)
        assert (p["source_id"], p["target_id"]) == ("2", "99")

    def test_the_receiving_half_survives(self):
        # The deposit carries the receiving bank's stable external_id; the paying side's
        # is derived from its statement position and is worthless once the row is gone.
        p = lt.plan(canonical(), OWN)[0][0]
        assert p["keep"] == "7270"
        assert p["drop"] == "7268"
        assert p["keep_split"]["external_id"] == "413A8C41CEB0443C8ADECAA6423F8945"

    def test_source_comes_from_the_withdrawal_not_the_deposit(self):
        # The deposit's own source is a revenue account (27) and must not survive.
        p = lt.plan(canonical(), OWN)[0][0]
        assert p["source_id"] == "2"


class TestLeftAlone:
    def test_a_lone_withdrawal_is_silent(self):
        # Already reported by the nightly check; repeating it here would mail nightly.
        assert lt.plan([group("1", [wd()])], OWN) == ([], [])

    def test_a_lone_deposit_is_silent(self):
        assert lt.plan([group("1", [dp()])], OWN) == ([], [])

    def test_different_amounts_do_not_pair(self):
        assert lt.plan([group("1", [wd()]), group("2", [dp(amount="1300.00")])], OWN) == ([], [])

    def test_different_dates_do_not_pair(self):
        assert lt.plan([group("1", [wd()]), group("2", [dp(date="2026-10-27")])], OWN) == ([], [])

    def test_a_deposit_into_a_foreign_account_is_ignored(self):
        assert lt.plan([group("1", [wd()]), group("2", [dp(dest="999")])], OWN) == ([], [])

    def test_a_payee_without_an_own_iban_is_ignored(self):
        plain = group("1", [wd(payee="REWE SAGT DANKE")])
        assert lt.plan([plain, group("2", [dp()])], OWN) == ([], [])

    def test_an_existing_transfer_is_ignored(self):
        already = dict(dp()); already["type"] = "transfer"
        assert lt.plan([group("1", [wd()]), group("2", [already])], OWN) == ([], [])

    def test_a_split_transaction_is_never_touched(self):
        # Deliberate structure. Converting or deleting one is not a mechanical fix.
        assert lt.plan([group("1", [wd(), wd()]), group("2", [dp()])], OWN) == ([], [])


class TestRefusals:
    def test_two_withdrawals_for_one_deposit(self):
        pairs, refusals = lt.plan(
            [group("1", [wd(journal="a", ext="2026-10-26.0")]),
             group("2", [wd(journal="b", ext="2026-10-26.9")]),
             group("3", [dp()])], OWN)
        assert pairs == []
        assert len(refusals) == 1 and "ambiguous" in refusals[0]
        assert "'1', '2'" in refusals[0] or "1" in refusals[0]

    def test_two_deposits_for_one_withdrawal(self):
        pairs, refusals = lt.plan(
            [group("1", [wd()]),
             group("2", [dp(journal="a", ext="A")]),
             group("3", [dp(journal="b", ext="B")])], OWN)
        assert pairs == []
        assert len(refusals) == 1 and "ambiguous" in refusals[0]

    def test_payee_carrying_its_own_source_iban(self):
        # Would otherwise plan a transfer from an account to itself.
        odd = group("1", [wd(src="99")])
        pairs, refusals = lt.plan([odd, group("2", [dp()])], OWN)
        assert pairs == []
        assert len(refusals) == 1 and "own source account" in refusals[0]

    def test_an_ambiguous_day_does_not_block_a_clean_one(self):
        clean_w = wd(date="2026-09-28", journal="x", ext="2026-09-28.0")
        clean_d = dp(date="2026-09-28", journal="y", ext="GUID-SEP")
        pairs, refusals = lt.plan(
            [group("1", [wd()]), group("2", [wd(journal="b", ext="dup")]), group("3", [dp()]),
             group("4", [clean_w]), group("5", [clean_d])], OWN)
        assert len(pairs) == 1 and pairs[0]["date"] == "2026-09-28"
        assert len(refusals) == 1


class TestPayload:
    def test_converts_the_kept_journal_to_a_transfer(self):
        p = lt.plan(canonical(), OWN)[0][0]
        body = lt.transfer_payload(p)["transactions"][0]
        assert body["transaction_journal_id"] == "7356"
        assert body["type"] == "transfer"
        assert body["source_id"] == "2"
        assert body["destination_id"] == "99"

    def test_leaves_the_banks_own_wording_alone(self):
        # A job has nothing better to put in the description, and inventing a label
        # would hide what the statement said.
        body = lt.transfer_payload(lt.plan(canonical(), OWN)[0][0])["transactions"][0]
        assert "description" not in body
        assert "category_id" not in body and "category_name" not in body


class TestDedupe:
    def test_a_group_on_a_page_boundary_is_not_offered_twice(self):
        g = group("7268", [wd()])
        assert [x["id"] for x in lt.dedupe([g, g, group("7270", [dp()])])] == ["7268", "7270"]

    def test_a_duplicated_half_still_pairs_exactly_once(self):
        w, d = group("7268", [wd()]), group("7270", [dp()])
        pairs, refusals = lt.plan(lt.dedupe([w, d, w, d]), OWN)
        assert len(pairs) == 1 and refusals == []
