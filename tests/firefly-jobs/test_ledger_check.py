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
