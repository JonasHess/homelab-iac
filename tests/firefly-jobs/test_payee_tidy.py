import pytest
from conftest import _load

pt = _load("payee_tidy")

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
        assert pt.merchant(WRAPPED_AT_SPACE) == "Beispiel Handel (shop.example)"

    def test_a_word_broken_at_a_line_break_is_rejoined(self):
        assert pt.merchant(WRAPPED_IN_WORD) == "Futterwelt GmbH"

    def test_an_unwrapped_description(self):
        assert pt.merchant(UNWRAPPED) == "Fahrrad Laden GMBH"

    def test_the_clean_first_copy_wins_over_a_badly_wrapped_repeat(self):
        assert pt.merchant(REPEATED) == "Kino Karten GmbH"

    def test_a_space_inserted_at_a_break_is_dropped(self):
        assert pt.merchant(SPACE_INSERTED) == "Mars Matratzen GmbH"

    def test_only_spaces_every_copy_agrees_on_are_kept(self):
        assert pt.merchant(SPACE_INSERTED_THRICE) == "Bahn Vertrieb GmbH"

    def test_a_break_both_copies_share_is_a_space_before_a_capital(self):
        assert pt.merchant(SAME_BREAK) == "Nordsee Post AG"

    def test_a_cut_at_the_fixed_line_width_is_undone(self):
        assert pt.merchant(SAME_CUT) == "Mars Matratzen AG"

    def test_an_upper_case_description(self):
        assert pt.merchant(UPPER) == "KINO KARTEN GMBH"

    def test_a_card_payment(self):
        assert pt.merchant(CARD) == "MARKTSTAND"

    @pytest.mark.parametrize("desc", [PRIVATE, PRIVATE_SEPA])
    def test_a_payment_to_a_person_has_no_merchant(self, desc):
        # PayPal leaves the name out for payments to private people; only PayPal's own
        # export knows it, so the row stays with PayPal.
        assert pt.merchant(desc) is None

    def test_a_single_break_in_a_word_is_rejoined_by_the_other_copy(self):
        # Only one line break, and it splits the name; the second copy is unbroken.
        desc = ("1000000000010/PP.1111.PP/. Weltrei; sen, Ihr Einkauf bei Weltreisen"
                " Lastschrift / Belastung")
        assert pt.merchant(desc) == "Weltreisen"

    def test_unrelated_text_has_no_merchant(self):
        assert pt.merchant("Miete November") is None


def split(gid, dest="PayPal Europe S.a.r.l. et Cie S.C.", desc=UNWRAPPED, typ="withdrawal", jid="j1",
          dest_id="1"):
    return {"id": gid, "attributes": {"transactions": [{
        "transaction_journal_id": jid, "type": typ, "date": "2026-11-01T00:00:00+01:00",
        "amount": "10.00", "destination_id": dest_id, "destination_name": dest, "description": desc}]}}


class TestPlan:
    def test_a_paypal_withdrawal_gets_its_merchant(self):
        changes = pt.plan([split("1")])
        assert changes == [{"group": "1", "journal": "j1", "date": "2026-11-01", "amount": "10.00",
                            "old": "PayPal Europe S.a.r.l. et Cie S.C.", "new": "Fahrrad Laden GMBH"}]

    def test_an_already_named_row_is_left_alone(self):
        assert pt.plan([split("1", dest="Fahrrad Laden GMBH")]) == []

    def test_a_private_payment_stays_with_paypal(self):
        assert pt.plan([split("1", desc=PRIVATE)]) == []

    def test_deposits_and_splits_are_left_alone(self):
        assert pt.plan([split("1", typ="deposit")]) == []
        g = split("2"); g["attributes"]["transactions"].append(dict(g["attributes"]["transactions"][0]))
        assert pt.plan([g]) == []

    def test_a_group_listed_twice_is_changed_once(self):
        assert len(pt.plan([split("1"), split("1")])) == 1

    def test_an_existing_payee_spelled_differently_is_reused(self):
        payees = {"7": "Fahrrad Laden GmbH", "8": "Kino Karten GmbH"}
        change = pt.plan([split("1", desc=UNWRAPPED)], payees)[0]
        assert (change["new"], change["payee_id"]) == ("Fahrrad Laden GmbH", "7")
        assert pt.payload(change)["transactions"][0] == {"transaction_journal_id": "j1",
                                                         "destination_id": "7"}

    def test_a_row_already_on_the_matching_payee_is_left_alone(self):
        assert pt.plan([split("1", dest="Fahrrad Laden GmbH")], {"7": "Fahrrad Laden GmbH"}) == []

    def test_of_several_spellings_the_readable_one_wins(self):
        names = {"3": "fahrrad laden gmbh", "4": "FahrradLadenGmbH", "5": "Fahrrad Laden GmbH",
                 "6": "FAHRRAD LADEN GMBH"}
        assert pt.preferred(names) == "5"

    def test_the_payload_changes_only_the_payee(self):
        body = pt.payload(pt.plan([split("1")])[0])
        assert body == {"apply_rules": False, "fire_webhooks": False,
                        "transactions": [{"transaction_journal_id": "j1",
                                          "destination_name": "Fahrrad Laden GMBH"}]}


class TestSpellingVariants:
    # One merchant as two banks spell it on card payments.
    PAYEES = {"8": "Imbiss//Musterstadt/DE", "9": "IMBISS//MUSTERSTADT/DE", "10": "Kiosk Nord"}

    def test_a_payment_on_a_second_spelling_moves_to_the_readable_one(self):
        change = pt.plan([split("1", dest="IMBISS//MUSTERSTADT/DE", dest_id="9", desc="Kartenzahlung")],
                         self.PAYEES)[0]
        assert (change["payee_id"], change["new"], change["old"]) == \
            ("8", "Imbiss//Musterstadt/DE", "IMBISS//MUSTERSTADT/DE")

    def test_a_payment_already_on_the_readable_spelling_is_left_alone(self):
        assert pt.plan([split("1", dest="Imbiss//Musterstadt/DE", dest_id="8", desc="x")], self.PAYEES) == []

    def test_a_payee_without_variants_is_left_alone(self):
        assert pt.plan([split("1", dest="Kiosk Nord", dest_id="10", desc="x")], self.PAYEES) == []

    def test_payees_kept_apart_on_purpose_are_never_merged(self):
        # e.g. one person holding two accounts at the same bank
        rows = [split("1", dest="IMBISS//MUSTERSTADT/DE", dest_id="9", desc="x")]
        assert pt.plan(rows, self.PAYEES, ignore_ids={"9"}) == []
        assert pt.plan(rows, self.PAYEES, ignore_ids={"8"}) == []

    def test_umlauts_and_their_transcriptions_are_one_spelling(self):
        payees = {"3": "B\u00e4ckerei M\u00fcller", "4": "BAECKEREI MUELLER"}
        change = pt.plan([split("1", dest="BAECKEREI MUELLER", dest_id="4", desc="x")], payees)[0]
        assert change["payee_id"] == "3"
