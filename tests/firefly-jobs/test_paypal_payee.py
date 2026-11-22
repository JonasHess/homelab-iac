import pytest
from conftest import _load

pp = _load("paypal_payee")

# Formats as the banks deliver them; merchants and references are invented.
WRAPPED_AT_SPACE = ("1000000000001/PP.1111.PP/. Beispiel; Handel (shop.example), Ihr Einkauf be;"
                    " i Beispiel Handel (shop.example) Lastschrift / Belastung")
WRAPPED_IN_WORD = ("1000000000002/PP.1111.PP/. Futterwel; t GmbH, Ihr Einkauf bei Futterw;"
                   " elt GmbH Lastschrift / Belastung")
UNWRAPPED = ("1000000000003/PP.2222.PP/. Fahrrad Laden GMBH, Ihr Einkauf bei Fahrrad Laden GMBH"
             " EREF: 1000000000003 MREF: XX0000 CRED: LU00ZZZ0000")
REPEATED = ("1000000000004/. Kino Karten GmbH, Ihr Einkauf bei Kino Karten GmbH SVWZ+"
            " 1000000000004/. Kino K arten GmbH, Ihr Einkauf bei Kin o Karten GmbH")
SPACE_INSERTED = ("1000000000007/PP.1111.PP/. Mars Matr atzen GmbH, Ihr Einkauf bei Mars M atratzen"
                  " GmbH")
SPACE_INSERTED_THRICE = ("1000000000008/PP.3333.PP/. Bahn Vertr ieb GmbH, Ihr Einkauf bei Bahn Vertri eb"
                         " GmbH SVWZ+ 1000000000008/PP.3333.PP/. Bahn Vertrieb GmbH, Ihr Einka uf bei"
                         " Bahn Vertrieb GmbH")
# Both copies break between the two words, so neither can settle the other; the capital
# that starts the second word does.
SAME_BREAK = ("1000000000009/PP.1111.PP/. Nordsee; Post AG, Ihr Einkauf bei Nordsee; Post AG"
              " Lastschrift / Belastung")
# Lines are cut at 35 characters and joined with a space, and both copies happen to be
# cut inside the same word, so neither can settle the other.
SAME_CUT = "1000000000011/PP.1111.PP/. Mars Mat ratzen AG, Ihr Einkauf bei Mars Mat ratzen AG"
UPPER = "1000000000012/. KINO KARTEN GMBH, IHR EINKAUF BEI KINO KARTEN GMBH1000000000012/. KINO"
CARD = "PAYPAL *MARKTSTAND STADT"
PRIVATE = "1000000000005/PP.1111.PP/. , Ihr Ei nkauf bei"
PRIVATE_SEPA = "1000000000006/PP.2222.PP/. , Ihr Einkauf bei EREF: 1000000000006 MREF: XX0000"


class TestMerchant:
    def test_a_space_lost_at_a_line_break_is_restored(self):
        assert pp.merchant(WRAPPED_AT_SPACE) == "Beispiel Handel (shop.example)"

    def test_a_word_broken_at_a_line_break_is_rejoined(self):
        assert pp.merchant(WRAPPED_IN_WORD) == "Futterwelt GmbH"

    def test_an_unwrapped_description(self):
        assert pp.merchant(UNWRAPPED) == "Fahrrad Laden GMBH"

    def test_the_clean_first_copy_wins_over_a_badly_wrapped_repeat(self):
        assert pp.merchant(REPEATED) == "Kino Karten GmbH"

    def test_a_space_inserted_at_a_break_is_dropped(self):
        assert pp.merchant(SPACE_INSERTED) == "Mars Matratzen GmbH"

    def test_only_spaces_every_copy_agrees_on_are_kept(self):
        assert pp.merchant(SPACE_INSERTED_THRICE) == "Bahn Vertrieb GmbH"

    def test_a_break_both_copies_share_is_a_space_before_a_capital(self):
        assert pp.merchant(SAME_BREAK) == "Nordsee Post AG"

    def test_a_cut_at_the_fixed_line_width_is_undone(self):
        assert pp.merchant(SAME_CUT) == "Mars Matratzen AG"

    def test_an_upper_case_description(self):
        assert pp.merchant(UPPER) == "KINO KARTEN GMBH"

    def test_a_card_payment(self):
        assert pp.merchant(CARD) == "MARKTSTAND"

    @pytest.mark.parametrize("desc", [PRIVATE, PRIVATE_SEPA])
    def test_a_payment_to_a_person_has_no_merchant(self, desc):
        # PayPal leaves the name out for payments to private people; only PayPal's own
        # export knows it, so the row stays with PayPal.
        assert pp.merchant(desc) is None

    def test_unrelated_text_has_no_merchant(self):
        assert pp.merchant("Miete November") is None


def split(gid, dest="PayPal Europe S.a.r.l. et Cie S.C.", desc=UNWRAPPED, typ="withdrawal", jid="j1"):
    return {"id": gid, "attributes": {"transactions": [{
        "transaction_journal_id": jid, "type": typ, "date": "2026-11-01T00:00:00+01:00",
        "amount": "10.00", "destination_name": dest, "description": desc}]}}


    def test_a_single_break_in_a_word_is_rejoined_by_the_other_copy(self):
        # Only one line break, and it splits the name; the second copy is unbroken.
        desc = ("1000000000010/PP.1111.PP/. Weltrei; sen, Ihr Einkauf bei Weltreisen"
                " Lastschrift / Belastung")
        assert pp.merchant(desc) == "Weltreisen"


class TestPlan:
    def test_a_paypal_withdrawal_gets_its_merchant(self):
        changes = pp.plan([split("1")])
        assert changes == [{"group": "1", "journal": "j1", "date": "2026-11-01", "amount": "10.00",
                            "old": "PayPal Europe S.a.r.l. et Cie S.C.", "new": "Fahrrad Laden GMBH"}]

    def test_an_already_named_row_is_left_alone(self):
        assert pp.plan([split("1", dest="Fahrrad Laden GMBH")]) == []

    def test_a_private_payment_stays_with_paypal(self):
        assert pp.plan([split("1", desc=PRIVATE)]) == []

    def test_deposits_and_splits_are_left_alone(self):
        assert pp.plan([split("1", typ="deposit")]) == []
        g = split("2"); g["attributes"]["transactions"].append(dict(g["attributes"]["transactions"][0]))
        assert pp.plan([g]) == []

    def test_a_group_listed_twice_is_changed_once(self):
        assert len(pp.plan([split("1"), split("1")])) == 1

    def test_an_existing_payee_spelled_differently_is_reused(self):
        payees = {"7": "Fahrrad Laden GmbH", "8": "Kino Karten GmbH"}
        change = pp.plan([split("1", desc=UNWRAPPED)], payees)[0]
        assert (change["new"], change["payee_id"]) == ("Fahrrad Laden GmbH", "7")
        assert pp.payload(change)["transactions"][0] == {"transaction_journal_id": "j1",
                                                         "destination_id": "7"}

    def test_a_row_already_on_the_matching_payee_is_left_alone(self):
        assert pp.plan([split("1", dest="Fahrrad Laden GmbH")], {"7": "Fahrrad Laden GmbH"}) == []

    def test_of_several_spellings_the_readable_one_wins(self):
        names = {"3": "fahrrad laden gmbh", "4": "FahrradLadenGmbH", "5": "Fahrrad Laden GmbH",
                 "6": "FAHRRAD LADEN GMBH"}
        assert pp.preferred(names) == "5"

    def test_the_payload_changes_only_the_payee(self):
        body = pp.payload(pp.plan([split("1")])[0])
        assert body == {"apply_rules": False, "fire_webhooks": False,
                        "transactions": [{"transaction_journal_id": "j1",
                                          "destination_name": "Fahrrad Laden GMBH"}]}
