import csv, io
import pytest
from conftest import _load

fi = _load("fints_import")


def aq_row(**kw):
    """One row as AqBanking writes it with the job's export profile."""
    row = {"date": "2026-10-08", "valutaDate": "2026-10-07", "value_value": "-12.5",
           "value_currency": "EUR", "remoteName": "REWE SAGT DANKE.",
           "remoteIban": "DE54700202700015563401", "remoteBic": "",
           "transactionText": "Debitkartenzahlung", "purpose": "SVWZ+2026-10-07T18:14",
           "purpose1": "Debitk.0 2028-12", "purpose2": "", "purpose3": "", "purpose9": "",
           "endToEndReference": "", "mandateId": "", "creditorSchemeId": "",
           "type": "statement"}
    row.update(kw)
    return row


class TestAmount:
    @pytest.mark.parametrize("raw,want", [
        ("-12.5", "-12,50"), ("2031.78", "2.031,78"), ("-23000", "-23.000,00"),
        ("0.07", "0,07"), ("-1234567.891", "-1.234.567,89")])
    def test_formats_like_the_web_export(self, raw, want):
        assert fi.de_amount(raw) == want


class TestConvert:
    def test_maps_into_the_web_export_columns(self):
        out = fi.convert([aq_row()], since="2025-11-17")
        assert len(out) == 1
        r = out[0]
        assert list(r) == fi.HEADER
        assert r["Buchungstag"] == "08.10.2026"
        assert r["Wertstellung"] == "07.10.2026"
        assert r["Soll/Haben"] == "-12,50"
        assert r["Buchungsart"] == "Debitkartenzahlung"
        assert r["Empf\u00e4nger/Auftraggeber Name"] == "REWE SAGT DANKE."
        assert r["Empf\u00e4nger/Auftraggeber IBAN"] == "DE54700202700015563401"
        assert [r[f"Vwz.{i}"] for i in range(4)] == ["SVWZ+2026-10-07T18:14", "Debitk.0 2028-12", "", ""]

    def test_keeps_long_purposes(self):
        row = aq_row(purpose4="AWV-MELDEPFLICHT", purpose9="last line")
        r = fi.convert([row], since="2025-11-17")[0]
        assert (r["Vwz.4"], r["Vwz.9"]) == ("AWV-MELDEPFLICHT", "last line")

    def test_drops_pending_rows(self):
        assert fi.convert([aq_row(type="notedStatement")], since="2025-11-17") == []

    def test_drops_zero_amounts(self):
        assert fi.convert([aq_row(value_value="0")], since="2025-11-17") == []

    def test_drops_rows_before_the_cutover(self):
        # Older rows came from the PDF and web history in a different wording; booking
        # them again from FinTS would duplicate every one of them.
        rows = [aq_row(date="2025-11-16"), aq_row(date="2025-11-17")]
        assert [r["Buchungstag"] for r in fi.convert(rows, since="2025-11-17")] == ["17.11.2025"]

    def test_missing_valuta_stays_empty(self):
        assert fi.convert([aq_row(valutaDate="")], since="2025-11-17")[0]["Wertstellung"] == ""


class TestClassify:
    def test_success(self):
        log = "Sending job JobGetTransactions\nYour bank does not send an opening saldo\n"
        assert fi.classify(log, 0) == "ok"

    def test_tan_demanded(self):
        log = "Challenge contains no optical data\ncgui.c: 545: No TAN input in non-interactive mode\n"
        assert fi.classify(log, 0) == "tan_required"

    def test_strong_authentication_code(self):
        assert fi.classify("HBCI: 9075 - Dialog abgebrochen - starke Authentifizierung erforderlich. (M)", 0) == "tan_required"

    @pytest.mark.parametrize("line", [
        "HBCI: 9942 - PIN falsch. (S)",
        "HBCI: 9931 - Sperrung des Kontos nach 3 Fehlversuchen (S)",
        "HBCI: 3938 - Ihr Zugang ist vorl\u00e4ufig gesperrt - Bitte PIN-Sperre aufheben. (W)",
        "HBCI: 9010 - Anmeldename und/oder Online-Banking-PIN ist falsch. (S)",
    ])
    def test_login_refused(self, line):
        assert fi.classify(line, 0) == "auth_failed"

    def test_login_refusal_wins_over_tan(self):
        log = "HBCI: 9942 - PIN falsch. (S)\nNo TAN input in non-interactive mode\n"
        assert fi.classify(log, 0) == "auth_failed"

    def test_nonzero_exit_without_known_cause(self):
        assert fi.classify("something else\n", 3) == "error"


class TestWrite:
    def test_round_trip_keeps_quoting_and_header(self, tmp_path):
        target = tmp_path / "out.csv"
        fi.write_csv(fi.convert([aq_row()], since="2025-11-17"), target)
        text = target.read_text(encoding="utf-8")
        assert text.splitlines()[0].startswith('"Kontonummer";"Datum/Zeit";"Buchungstag"')
        back = list(csv.DictReader(io.StringIO(text), delimiter=";"))
        assert back[0]["Soll/Haben"] == "-12,50"
        assert not list(tmp_path.glob(".*"))  # temp file renamed away

    def test_header_only_when_nothing_new(self, tmp_path):
        target = tmp_path / "out.csv"
        fi.write_csv([], target)
        assert len(target.read_text(encoding="utf-8").splitlines()) == 1
