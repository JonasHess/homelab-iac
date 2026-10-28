import pytest
from conftest import _load

lt = _load("link_transfers")

# The real shape of the case this job exists for: a standing monthly transfer that both
# banks report, with the paying bank naming the payee after the receiving IBAN.
BBBANK = "DE00100000000000000002"
COMDIR = "DE00100000000000000001"
OWN = {BBBANK: {"id": "2", "name": "Bank A Giro"},
       COMDIR: {"id": "99", "name": "Bank B Haushalt"}}

SELF_PAYEE = f"Kontoinhaber ({COMDIR})"


def group(gid, split_list):
    return {"id": gid, "attributes": {"transactions": split_list}}


def wd(amount="1350.00", date="2026-10-26", src="2", payee=SELF_PAYEE,
       journal="7354", ext="2026-10-26.0"):
    return {"transaction_journal_id": journal, "type": "withdrawal", "date": date + "T00:00:00+01:00",
            "amount": amount, "source_id": src, "source_name": "Bank A Giro",
            "destination_id": "601", "destination_name": payee,
            "description": "Haushalt Uebertrag", "external_id": ext}


def dp(amount="1350.00", date="2026-10-26", dest="99", journal="7356",
       ext="00000000000000000000000000000001"):
    return {"transaction_journal_id": journal, "type": "deposit", "date": date + "T00:00:00+01:00",
            "amount": amount, "source_id": "27", "source_name": "Kontoinhaber",
            "destination_id": dest, "destination_name": "Bank B Haushalt",
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
        spaced = "Kontoinhaber DE00 1000 0000 0000 0000 01"
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
        assert p["keep_split"]["external_id"] == "00000000000000000000000000000001"

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


# Broker is the real case: fed by hand from a broker export, so the deposit half
# of a transfer into it never arrives and it holds no IBAN in Firefly at all.
TR_IBAN = "DE00100000000000000003"
SOLE = {TR_IBAN: {"id": "257", "name": "Broker"}}
TR_PAYEE = f"Kontoinhaber ({TR_IBAN})"


def wd_tr(amount="3000.00", date="2026-10-27", src="2", payee=TR_PAYEE, journal="7363"):
    return {"transaction_journal_id": journal, "type": "withdrawal",
            "date": date + "T00:00:00+01:00", "amount": amount,
            "source_id": src, "source_name": "Bank A Giro",
            "destination_id": "5", "destination_name": payee,
            "description": "Aktiendepot", "external_id": "2026-10-27.3"}


class TestSoleSourceHalves:
    def test_a_lone_half_is_converted(self):
        converts, refusals = lt.sole_source_halves([group("7277", [wd_tr()])], SOLE)
        assert refusals == []
        assert len(converts) == 1
        c = converts[0]
        assert (c["group"], c["source_id"], c["target_id"]) == ("7277", "2", "257")
        assert c["cents"] == 300000

    def test_without_configuration_nothing_happens(self):
        assert lt.sole_source_halves([group("7277", [wd_tr()])], {}) == ([], [])
        assert lt.sole_source_halves([group("7277", [wd_tr()])], None) == ([], [])

    def test_several_on_one_day_are_all_converted(self):
        # Two genuine transfers of the same size on the same day are not ambiguous here:
        # there is no second half to pick between, each one simply needs retyping.
        converts, refusals = lt.sole_source_halves(
            [group("1", [wd_tr(journal="a")]), group("2", [wd_tr(journal="b")])], SOLE)
        assert refusals == []
        assert sorted(c["group"] for c in converts) == ["1", "2"]

    def test_a_payee_for_another_account_is_ignored(self):
        assert lt.sole_source_halves([group("1", [wd_tr(payee=SELF_PAYEE)])], SOLE) == ([], [])

    def test_a_plain_payee_is_ignored(self):
        assert lt.sole_source_halves([group("1", [wd_tr(payee="REWE")])], SOLE) == ([], [])

    def test_deposits_and_transfers_are_ignored(self):
        already = dict(wd_tr()); already["type"] = "transfer"
        assert lt.sole_source_halves([group("1", [already])], SOLE) == ([], [])
        assert lt.sole_source_halves([group("2", [dp()])], SOLE) == ([], [])

    def test_a_split_transaction_is_never_touched(self):
        assert lt.sole_source_halves([group("1", [wd_tr(), wd_tr()])], SOLE) == ([], [])

    def test_a_matching_deposit_blocks_the_conversion(self):
        # The other side was posted after all; which half to keep is then a decision.
        other = {"transaction_journal_id": "z", "type": "deposit",
                 "date": "2026-10-27T00:00:00+01:00", "amount": "3000.00",
                 "source_id": "27", "source_name": "Kontoinhaber",
                 "destination_id": "257", "destination_name": "Broker",
                 "description": "Incoming", "external_id": "tr-x"}
        converts, refusals = lt.sole_source_halves(
            [group("7277", [wd_tr()]), group("9", [other])], SOLE)
        assert converts == []
        assert len(refusals) == 1 and "matching deposit" in refusals[0]

    def test_a_deposit_of_another_amount_does_not_block(self):
        other = {"transaction_journal_id": "z", "type": "deposit",
                 "date": "2026-10-27T00:00:00+01:00", "amount": "99.00",
                 "source_id": "27", "source_name": "x",
                 "destination_id": "257", "destination_name": "Broker",
                 "description": "Incoming", "external_id": "tr-y"}
        converts, refusals = lt.sole_source_halves(
            [group("7277", [wd_tr()]), group("9", [other])], SOLE)
        assert len(converts) == 1 and refusals == []

    def test_payee_carrying_its_own_source_iban(self):
        converts, refusals = lt.sole_source_halves([group("1", [wd_tr(src="257")])], SOLE)
        assert converts == []
        assert len(refusals) == 1 and "own source account" in refusals[0]


class TestConvertPayload:
    def test_retypes_only_type_and_destination(self):
        c = lt.sole_source_halves([group("7277", [wd_tr()])], SOLE)[0][0]
        body = lt.convert_payload(c)["transactions"][0]
        assert body == {"transaction_journal_id": "7363", "type": "transfer",
                        "source_id": "2", "destination_id": "257"}

    def test_leaves_description_category_and_external_id_alone(self):
        # The paying bank's external_id is what the broker import's duplicate filter
        # later recognises the row by, so it must survive untouched.
        body = lt.convert_payload(
            lt.sole_source_halves([group("7277", [wd_tr()])], SOLE)[0][0])["transactions"][0]
        for f in ("description", "category_id", "category_name", "external_id"):
            assert f not in body


class TestJoinAndConvertDoNotOverlap:
    def test_the_pairing_pass_ignores_a_sole_source_payee(self):
        # 257 holds no IBAN in Firefly, so it never enters the ledger-read map and the
        # join path cannot see it. The two passes therefore cannot both claim a row.
        assert lt.plan([group("7277", [wd_tr()])], OWN) == ([], [])

    def test_the_conversion_pass_ignores_a_joinable_pair(self):
        assert lt.sole_source_halves(canonical(), SOLE) == ([], [])
