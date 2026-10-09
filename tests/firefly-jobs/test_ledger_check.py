from conftest import _load

lc = _load("ledger_check")

OWN = {"DE13200411440494963200": "Comdirect Haushalt",
       "DE90100123450119298501": "Trade Republic"}
LOAN_PAYEES = {"49", "50", "244"}


def split(**kw):
    base = {"type": "withdrawal", "date": "2026-10-05T00:00:00+02:00", "amount": "100.00",
            "source_id": "2", "source_name": "BBBank Giro", "destination_id": "900",
            "destination_name": "Some Shop", "description": "x",
            "category_name": "Shopping/Online", "external_id": "e1"}
    base.update(kw)
    return base


def grp(gid, *s):
    return {"id": gid, "attributes": {"transactions": list(s)}}


class TestDedupe:
    def test_repeated_group_counted_once(self):
        g = grp("1", split())
        assert len(lc.dedupe([g, g, g])) == 1

    def test_distinct_groups_kept(self):
        assert len(lc.dedupe([grp("1", split()), grp("2", split())])) == 2


class TestUnlinkedTransfers:
    def test_payee_name_carrying_an_own_iban(self):
        hits = lc.unlinked_transfers(
            [grp("10", split(destination_name="Michael Zimmermann (DE13200411440494963200)",
                             amount="1350.00"))], OWN, LOAN_PAYEES)
        assert len(hits) == 1
        assert "Comdirect Haushalt" in hits[0]

    def test_known_loan_payee_without_an_iban(self):
        # Every loan account has no IBAN in Firefly, so the name signature cannot
        # work and the payee id is the only handle.
        hits = lc.unlinked_transfers(
            [grp("11", split(destination_id="244", destination_name="Michael Zimmermann Tatjana"))],
            OWN, LOAN_PAYEES)
        assert len(hits) == 1 and "payee #244" in hits[0]

    def test_real_transfers_are_not_flagged(self):
        # An already-correct transfer must never be reported, or the job mails forever.
        hits = lc.unlinked_transfers(
            [grp("12", split(type="transfer", destination_id="99",
                             destination_name="Comdirect Haushalt"))], OWN, LOAN_PAYEES)
        assert hits == []

    def test_ordinary_expense_is_not_flagged(self):
        assert lc.unlinked_transfers([grp("13", split())], OWN, LOAN_PAYEES) == []

    def test_each_group_reported_once_even_if_both_sides_match(self):
        hits = lc.unlinked_transfers(
            [grp("14", split(source_name="Michael Zimmermann (DE90100123450119298501)",
                             destination_name="Michael Zimmermann (DE13200411440494963200)",
                             type="deposit"))], OWN, LOAN_PAYEES)
        assert len(hits) == 1


class TestDuplicates:
    def test_same_external_id_and_identical_row(self):
        s = split(external_id="ABC")
        definite, review = lc.duplicates([grp("20", s), grp("21", dict(s))])
        assert len(definite) == 1 and "ABC" in definite[0]
        assert review == 0

    def test_identical_rows_with_different_external_ids_are_review_only(self):
        # Four identical small card payments in one day are real; listing them would
        # mail noise every night, so they are counted and not named.
        definite, review = lc.duplicates(
            [grp("22", split(external_id="A")), grp("23", split(external_id="B"))])
        assert definite == []
        assert review == 1

    def test_distinct_transactions_are_clean(self):
        definite, review = lc.duplicates(
            [grp("24", split(external_id="A", amount="1.00")),
             grp("25", split(external_id="B", amount="2.00"))])
        assert (definite, review) == ([], 0)

    def test_missing_external_ids_never_count_as_definite(self):
        definite, _ = lc.duplicates(
            [grp("26", split(external_id="")), grp("27", split(external_id=None))])
        assert definite == []


class TestUncategorised:
    def test_lists_only_uncategorised_non_transfers(self):
        rows = lc.uncategorised([
            grp("30", split(category_name=None, destination_name="Edmund", amount="500.00")),
            grp("31", split(category_name="Lebensmittel")),
            # an own-account move legitimately has no category
            grp("32", split(type="transfer", category_name=None)),
        ])
        assert len(rows) == 1 and "Edmund" in rows[0]

    def test_counterparties_aggregate_and_sort_by_value(self):
        rows = lc.uncategorised([
            grp("33", split(category_name=None, destination_name="Small", amount="5.00")),
            grp("34", split(category_name=None, destination_name="Big", amount="900.00")),
            grp("35", split(category_name=None, destination_name="Big", amount="100.00")),
        ])
        assert "Big" in rows[0] and "1,000.00" in rows[0]
        assert "2x" in rows[0].replace(" ", "") or "2x" in rows[0]

    def test_clean_ledger_returns_nothing(self):
        assert lc.uncategorised([grp("36", split())]) == []


class TestDuplicatePayees:
    def test_iban_suffixed_twin(self):
        rows = lc.duplicate_payees([
            ("241", "revenue", "Anhelina Melnyk"),
            ("1013", "revenue", "Anhelina Melnyk (DE04508400050633343900)")])
        assert len(rows) == 1 and "#241" in rows[0] and "#1013" in rows[0]

    def test_case_only_twin(self):
        # Hetzner really exists twice this way; `contains` rules hide it, reports should not.
        rows = lc.duplicate_payees([
            ("1", "expense", "Hetzner Online GmbH"),
            ("2", "expense", "HETZNER ONLINE GMBH")])
        assert len(rows) == 1

    def test_revenue_and_expense_pair_is_not_a_duplicate(self):
        # Firefly keeps the two namespaces apart; one of each is correct for anyone you
        # both pay and receive from. Flagging these would mail noise forever.
        assert lc.duplicate_payees([
            ("61", "revenue", "HUANLUN SUN"),
            ("571", "expense", "Huanlun Sun")]) == []

    def test_distinct_payees_are_clean(self):
        assert lc.duplicate_payees([
            ("1", "expense", "REWE Gladenbach"),
            ("2", "expense", "ALDI Lorsch")]) == []

    def test_three_way_group_reported_once(self):
        rows = lc.duplicate_payees([
            ("1", "expense", "Michael Zimmermann"),
            ("2", "expense", "Michael Zimmermann (DE13200411440494963200)"),
            ("3", "expense", "Michael Zimmermann (DE28760909009652752401)")])
        assert len(rows) == 1 and rows[0].count("#") == 3

    def test_whitespace_and_punctuation_only_twin(self):
        rows = lc.duplicate_payees([
            ("1", "expense", "Vodafone West GmbH"),
            ("2", "expense", "VodafoneWestGmbH")])
        assert len(rows) == 1

    def test_ignored_counterparty_is_not_reported(self):
        # The self-payees are kept on purpose - they are what the unlinked-transfer
        # scan matches on - so reporting them would mail every night forever.
        payees = [("4", "expense", "Michael Zimmermann"),
                  ("50", "expense", "Michael Zimmermann (DE55760909009652752400)")]
        assert lc.duplicate_payees(payees) != []
        assert lc.duplicate_payees(payees, ignore=["Michael Zimmermann"]) == []

    def test_ignore_matches_on_the_normalised_name(self):
        payees = [("1", "expense", "Jonas Hess"),
                  ("141", "expense", "Jonas Hess (DE50500105175425117339)")]
        assert lc.duplicate_payees(payees, ignore=["jonas  hess"]) == []

    def test_ignoring_one_name_does_not_hide_others(self):
        payees = [("4", "expense", "Michael Zimmermann"),
                  ("50", "expense", "Michael Zimmermann (DE5576)"),
                  ("1035", "expense", "HETZNER ONLINE GMBH"),
                  ("279", "expense", "Hetzner Online GmbH")]
        rows = lc.duplicate_payees(payees, ignore=["Michael Zimmermann"])
        assert len(rows) == 1 and "etzner" in rows[0].lower()
