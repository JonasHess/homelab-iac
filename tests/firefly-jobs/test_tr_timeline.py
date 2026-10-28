import pytest
from conftest import _load

tt = _load("tr_timeline")

# Synthetic fixtures only. Real timeline events carry names, e-mail addresses and IBANs.
CASH, DEPOT = "257", "258"
OWN_IBAN_SPACED = "DE00 1111 2222 3333 4444 55"
OWN_IBAN = "DE00111122223333444455"
OTHER_IBAN_SPACED = "DE00 9999 8888 7777 6666 55"


def ev(et, amount=None, sections=(), status="EXECUTED", eid="evt-1",
       ts="2026-05-04T10:00:00.000+0000", title="Beispiel"):
    """A timeline event. `sections` is a sequence of (title, {field: text}) so a test can
    give two sections the same field title, as real events do with IBAN."""
    secs = [{"type": "table", "title": t,
             "data": [{"title": k, "detail": {"text": v}} for k, v in f.items()]}
            for t, f in sections]
    return {"id": eid, "eventType": et, "timestamp": ts, "status": status, "title": title,
            "amount": None if amount is None else {"value": amount, "currency": "EUR"},
            "details": {"sections": secs}}


def run(event, **kw):
    return tt.payloads(event, CASH, DEPOT, **kw)


def cash_effect(ps):
    c = 0
    for p in ps:
        cents = int(round(float(p["amount"]) * 100))
        c += cents if p.get("destination_id") == CASH else -cents if p.get("source_id") == CASH else 0
    return c / 100


class TestParsing:
    @pytest.mark.parametrize("text,value", [
        ("5.831,00\xa0\u20ac", 5831.00),
        ("+ 0,45\xa0\u20ac", 0.45),
        ("-11,08\xa0\u20ac", -11.08),
        ("\u221239,75\xa0\u20ac", -39.75),
        ("1\xa0\u20ac", 1.0),
        ("Kostenlos", 0.0),
        ("Gratis", 0.0),
        (None, 0.0),
    ])
    def test_german_amounts(self, text, value):
        assert tt.de_amount(text) == pytest.approx(value)

    def test_full_iban_wins_over_the_masked_one_that_comes_first(self):
        e = ev("BANK_TRANSACTION_INCOMING", 100.0, [
            ("\u00dcbersicht", {"IBAN": "..4455"}),
            ("Absender", {"IBAN": OWN_IBAN_SPACED}),
        ])
        assert tt.counterparty_iban(e) == OWN_IBAN

    def test_only_a_masked_iban_is_no_iban(self):
        e = ev("BANK_TRANSACTION_INCOMING", 100.0, [("\u00dcbersicht", {"IBAN": "..4455"})])
        assert tt.counterparty_iban(e) is None

    def test_field_looks_across_sections(self):
        e = ev("ORDER_EXECUTED", -10.0, [("\u00dcbersicht", {"Asset": "Fonds A"}),
                                          ("Transaktion", {tt.FEE: "1,00 \u20ac"})])
        assert tt.field(e, tt.FEE) == "1,00 \u20ac"
        assert tt.field(e, "Asset") == "Fonds A"
        assert tt.field(e, "fehlt") is None


class TestFinality:
    def test_executed_is_final(self):
        assert tt.is_final(ev("CARD_TRANSACTION", -5.0))

    def test_canceled_is_not(self):
        assert not tt.is_final(ev("CARD_TRANSACTION", -5.0, status="CANCELED"))

    @pytest.mark.parametrize("state", ["Ausstehend", "Abgelehnt", "Storniert"])
    def test_pending_or_declined_in_the_detail_is_not(self, state):
        # The event status stays EXECUTED; only the detail says it is still open.
        e = ev("CARD_TRANSACTION", -5.0, [("\u00dcbersicht", {"Kartenzahlung": state})])
        assert not tt.is_final(e)
        assert run(e) == []


class TestCashInvariant:
    """Whatever a row is split into, the cash account must move by exactly the event's
    amount - that is what the broker's statement shows."""

    @pytest.mark.parametrize("event", [
        ev("CARD_TRANSACTION", -12.34, [("\u00dcbersicht", {"H\u00e4ndler": "Laden"})]),
        ev("CARD_REFUND", 9.99, [("\u00dcbersicht", {"H\u00e4ndler": "Laden"})]),
        ev("CARD_ATM_WITHDRAWAL", -150.0),
        ev("CARD_ORDER_FEE", -5.0),
        ev("PAYMENT_INBOUND", 500.0, [("\u00dcbersicht", {"Von": "Jemand", "IBAN": OTHER_IBAN_SPACED})]),
        ev("JUNIOR_P2P_TRANSFER", -51.0, [("\u00dcbersicht", {"Empf\u00e4nger": "Kind"})]),
        ev("INTEREST_PAYOUT", 72.88, [("\u00dcbersicht", {"Steuern": "26,12 \u20ac"})]),
        ev("SSP_CORPORATE_ACTION_CASH", 48.98, [("Gesch\u00e4ft", {"Steuer": "-11,08 \u20ac"})]),
        ev("SSP_CORPORATE_ACTION_CASH_NON_DIVIDEND", -39.75),
        ev("TAX_REFUND", 56.0),
        ev("ORDER_EXECUTED", -5831.0, [("Transaktion", {tt.FEE: "1,00 \u20ac"})]),
        ev("ORDER_EXECUTED", 19559.37, [("Transaktion", {"Steuern": "431,63 \u20ac",
                                                         tt.FEE: "1,00 \u20ac"})]),
        ev("SPARE_CHANGE_AGGREGATE", -1.0, [("\u00dcbersicht", {tt.FEE: "Kostenlos"})]),
        ev("GIFTER_TRANSACTION", -251.0, [("Transaktion", {tt.FEE: "1 \u20ac"})]),
        ev("REFERRAL_FIRST_TRADE_EXECUTED_INVITER", 25.0),
    ])
    def test_cash_moves_by_the_event_amount(self, event):
        ps = run(event)
        assert ps, "a money-moving event must produce payloads"
        assert cash_effect(ps) == pytest.approx(event["amount"]["value"])

    def test_saveback_nets_to_zero_on_cash(self):
        # Credited and invested at once; the event amount is the investment.
        ps = run(ev("SAVEBACK_AGGREGATE", -0.45, [("\u00dcbersicht", {"Asset": "Fonds A"})]))
        assert cash_effect(ps) == pytest.approx(0.0)


class TestTrades:
    def test_buy_amount_includes_the_fee(self):
        ps = run(ev("ORDER_EXECUTED", -5831.0, [("\u00dcbersicht", {"Asset": "Fonds A"}),
                                                 ("Transaktion", {tt.FEE: "1,00 \u20ac"})]))
        buy, fee = ps
        assert (buy["type"], buy["source_id"], buy["destination_id"]) == ("transfer", CASH, DEPOT)
        assert buy["amount"] == "5830.00"
        assert (fee["type"], fee["amount"], fee["category_name"]) == ("withdrawal", "1.00", tt.CAT_FEES)

    def test_sell_amount_is_net_of_fee_and_tax(self):
        # 19,559.37 reached cash; the securities were worth 19,992.00.
        ps = run(ev("ORDER_EXECUTED", 19559.37, [("\u00dcbersicht", {"Asset": "Fonds B"}),
                                                  ("Transaktion", {"Steuern": "431,63 \u20ac",
                                                                   tt.FEE: "1,00 \u20ac"})]))
        sell = next(p for p in ps if p["type"] == "transfer")
        assert (sell["source_id"], sell["destination_id"], sell["amount"]) == (DEPOT, CASH, "19992.00")
        assert {p["external_id"].split("-")[0] for p in ps} == {"tlx", "tlxtax", "tlxfee"}

    def test_a_fee_free_buy_produces_no_fee_row(self):
        ps = run(ev("SAVINGS_PLAN_EXECUTED", -150.0, [("Transaktion", {tt.FEE: "Gratis"})]))
        assert len(ps) == 1 and ps[0]["amount"] == "150.00"

    def test_a_holding_kept_outside_the_depot(self):
        ps = run(ev("IPO_TRADE_EXECUTED", -5820.52, [("\u00dcbersicht", {"Asset": "Neuemission",
                                                                          tt.FEE: "1,00 \u20ac"})]),
                 asset_accounts={"Neuemission": "1006"})
        assert ps[0]["destination_id"] == "1006" and ps[0]["amount"] == "5819.52"


class TestIncome:
    def test_interest_is_booked_gross_with_the_tax_apart(self):
        ps = run(ev("INTEREST_PAYOUT", 72.88, [("\u00dcbersicht", {"Steuern": "26,12 \u20ac"})]))
        income, tax = ps
        assert (income["type"], income["amount"], income["category_name"]) == ("deposit", "99.00", tt.CAT_YIELD)
        assert (tax["type"], tax["amount"], tax["destination_name"]) == ("withdrawal", "26.12", tt.PAYEE_TAX)

    def test_untaxed_interest_has_no_tax_row(self):
        assert len(run(ev("INTEREST_PAYOUT_CREATED", 22.20))) == 1

    def test_saveback_books_the_credit_and_the_purchase(self):
        ps = run(ev("SAVEBACK_AGGREGATE", -0.45, [("\u00dcbersicht", {"Asset": "Fonds A"})]))
        credit, buy = ps
        assert (credit["type"], credit["source_name"], credit["external_id"]) == ("deposit", "Fonds A", "tlxsb-evt-1")
        assert (buy["type"], buy["destination_id"], buy["external_id"]) == ("transfer", DEPOT, "tlx-evt-1")

    def test_gift_received_lands_in_the_depot_without_cash(self):
        ps = run(ev("GIFTING_RECIPIENT_ACTIVITY", None, [("Transaktion", {"Summe": "250,00 \u20ac"})]))
        assert len(ps) == 1
        assert (ps[0]["type"], ps[0]["destination_id"], ps[0]["amount"]) == ("deposit", DEPOT, "250.00")


class TestOwnAccounts:
    def incoming(self):
        return ev("BANK_TRANSACTION_INCOMING", 3000.0, [
            ("\u00dcbersicht", {"Absender": "Kontoinhaber", "IBAN": "..4455"}),
            ("Absender", {"IBAN": OWN_IBAN_SPACED}),
        ])

    def test_incoming_from_an_own_account_is_left_to_the_paying_bank(self):
        assert run(self.incoming(), own_accounts={OWN_IBAN}) == []

    def test_outgoing_to_an_own_account_is_left_out(self):
        out = ev("PAYMENT_OUTBOUND", -25000.0, [("\u00dcbersicht", {"An": "Kontoinhaber",
                                                                   "IBAN": OWN_IBAN_SPACED})])
        assert run(out, own_accounts={OWN_IBAN}) == []

    def test_without_own_ibans_it_is_an_ordinary_deposit(self):
        ps = run(self.incoming())
        assert (ps[0]["type"], ps[0]["destination_id"]) == ("deposit", CASH)

    def test_a_foreign_iban_is_an_ordinary_deposit(self):
        e = ev("PAYMENT_INBOUND", 500.0, [("\u00dcbersicht", {"Von": "Jemand", "IBAN": OTHER_IBAN_SPACED})])
        ps = run(e, own_accounts={OWN_IBAN})
        assert (ps[0]["type"], ps[0]["source_name"]) == ("deposit", "Jemand")


class TestNames:
    def test_alias_maps_onto_the_existing_payee(self):
        e = ev("CARD_TRANSACTION", -2.09, [("\u00dcbersicht", {"H\u00e4ndler": "Kurzname"})])
        ps = run(e, name_alias={"Kurzname": "Langer Name GmbH"})
        assert ps[0]["destination_name"] == "Langer Name GmbH"

    def test_an_unknown_name_is_kept(self):
        e = ev("CARD_TRANSACTION", -2.09, [("\u00dcbersicht", {"H\u00e4ndler": "Neu"})])
        assert run(e, name_alias={"Anders": "X"})[0]["destination_name"] == "Neu"


class TestNoMoney:
    @pytest.mark.parametrize("et", ["QUARTERLY_REPORT", "DEVICE_RESET", "DOCUMENTS_ACCEPTED",
                                    "SSP_CORPORATE_ACTION_ACTIVITY", "SOMETHING_NEW"])
    def test_events_that_move_no_money(self, et):
        assert run(ev(et, None)) == []

    def test_missing_id_or_date_is_skipped(self):
        assert run(ev("CARD_TRANSACTION", -5.0, eid=None)) == []
        assert run(ev("CARD_TRANSACTION", -5.0, ts="")) == []


class TestIds:
    def test_every_row_of_one_event_has_its_own_id(self):
        ps = run(ev("ORDER_EXECUTED", 100.0, [("Transaktion", {"Steuern": "5,00 \u20ac",
                                                               tt.FEE: "1,00 \u20ac"})]))
        ids = [p["external_id"] for p in ps]
        assert len(ids) == len(set(ids)) == 3

    def test_two_events_never_share_an_id(self):
        a = run(ev("CARD_TRANSACTION", -1.0, eid="a"))
        b = run(ev("CARD_TRANSACTION", -1.0, eid="b"))
        assert a[0]["external_id"] != b[0]["external_id"]


class TestUnhandled:
    def test_a_new_event_type_that_moves_money_is_flagged(self):
        assert tt.unhandled(ev("SOMETHING_NEW", -12.0))

    def test_a_known_type_is_not_flagged(self):
        assert not tt.unhandled(ev("CARD_TRANSACTION", -12.0))

    def test_an_own_account_movement_is_not_flagged(self):
        # Left out on purpose, not overlooked.
        e = ev("PAYMENT_INBOUND", 150.0, [("\u00dcbersicht", {"IBAN": OWN_IBAN_SPACED})])
        assert not tt.unhandled(e)

    def test_no_money_or_not_final_is_not_flagged(self):
        assert not tt.unhandled(ev("SOMETHING_NEW", None))
        assert not tt.unhandled(ev("SOMETHING_NEW", -12.0, status="CANCELED"))


class TestPlanPosts:
    def events(self):
        return [ev("CARD_TRANSACTION", -5.0, eid="old", ts="2026-01-01T10:00:00.000+0000"),
                ev("CARD_TRANSACTION", -6.0, eid="booked", ts="2026-10-20T10:00:00.000+0000"),
                ev("CARD_TRANSACTION", -7.0, eid="new", ts="2026-10-27T10:00:00.000+0000"),
                ev("SOMETHING_NEW", -8.0, eid="odd", ts="2026-10-27T10:00:00.000+0000")]

    def test_only_rows_in_the_window_and_not_yet_booked_are_posted(self):
        posts, odd = tt.plan_posts(self.events(), {"tlx-booked"}, "2026-10-01", CASH, DEPOT)
        assert [p["external_id"] for p in posts] == ["tlx-new"]

    def test_unhandled_events_in_the_window_are_reported(self):
        _, odd = tt.plan_posts(self.events(), set(), "2026-10-01", CASH, DEPOT)
        assert [e["id"] for e in odd] == ["odd"]

    def test_unhandled_events_before_the_window_are_not_reported_again(self):
        old = [ev("SOMETHING_NEW", -8.0, eid="odd", ts="2026-01-01T10:00:00.000+0000")]
        assert tt.plan_posts(old, set(), "2026-10-01", CASH, DEPOT) == ([], [])

    def test_a_derived_row_is_posted_even_when_its_parent_exists(self):
        # Fee and tax rows have ids of their own; one can be missing on its own.
        sell = ev("ORDER_EXECUTED", 100.0, [("Transaktion", {tt.FEE: "1,00 \u20ac"})],
                  eid="s", ts="2026-10-27T10:00:00.000+0000")
        posts, _ = tt.plan_posts([sell], {"tlx-s"}, "2026-10-01", CASH, DEPOT)
        assert [p["external_id"] for p in posts] == ["tlxfee-s"]

    def test_own_ibans_are_left_out(self):
        e = ev("PAYMENT_INBOUND", 150.0, [("\u00dcbersicht", {"IBAN": OWN_IBAN_SPACED})],
               ts="2026-10-27T10:00:00.000+0000")
        assert tt.plan_posts([e], set(), "2026-10-01", CASH, DEPOT,
                             own_accounts={OWN_IBAN}) == ([], [])


class TestOwnIbans:
    def accounts(self, monkeypatch, *attrs):
        monkeypatch.setattr(tt, "api", lambda path, params=None: {"data": [
            {"attributes": a} for a in attrs]})

    def test_iban_and_account_number_both_count(self, monkeypatch):
        # A bank merger gives the account a new IBAN; the old one lives on in history.
        self.accounts(monkeypatch, {"iban": OWN_IBAN_SPACED,
                                    "account_number": "DE00 9999 8888 7777 6666 55"})
        assert tt.own_ibans() == {OWN_IBAN, "DE00999988887777666655"}

    def test_an_account_number_that_is_no_iban_is_ignored(self, monkeypatch):
        self.accounts(monkeypatch, {"iban": None, "account_number": "12345678"})
        assert tt.own_ibans() == set()
