import datetime as dt

from conftest import _load

qr = _load("quality_report")

TAGS = {1: "Michael", 2: "Tatjana", 3: "Toni", 10: "Rechnungen & Belege", 11: "TradeRepublic"}
THEMES = {"Rechnungen & Belege"}
CORR = {5: "Unbekannt", 6: "Vodafone"}
TYPES = {7: "Unbekannt", 8: "Rechnung"}


def doc(i, tags=(1, 10), corr=6, typ=8, title="Rechnung 09/2026 DSL, 32,97 €"):
    return {"id": i, "title": title, "correspondent": corr, "document_type": typ, "tags": list(tags)}


def test_similar_finds_spelling_and_legal_form_variants():
    existing = ["Stadtverwaltung Bensheim", "Stadt Lorsch", "FNZ Bank", "Engel & Völkers", "Vodafone"]
    assert qr.similar("Vodafone GmbH", existing) == ["Vodafone"]
    assert qr.similar("Engel &amp; Völkers", existing) == ["Engel & Völkers"]
    assert qr.similar("Stadt Bensheim", existing) == ["Stadtverwaltung Bensheim"]
    assert qr.similar("Porkbun", existing) == []


def test_similar_ignores_the_name_itself():
    assert qr.similar("Vodafone", ["Vodafone"]) == []


def test_findings_per_document():
    docs = [
        doc(1),                                  # fine
        doc(2, tags=(10,)),                      # no person
        doc(3, tags=(1, 2, 10)),                 # Michael and Tatjana
        doc(4, corr=5),                          # correspondent Unbekannt
        doc(5, corr=None, typ=7),                # no correspondent, type Unbekannt
        doc(6, tags=(1, 11)),                    # only an importer tag, no theme
        doc(7, title="Kein Titel gefunden"),     # weak title
    ]
    f = qr.findings(docs, TAGS, CORR, TYPES, THEMES)
    assert [d["id"] for d in f["no_person"]] == [2]
    assert [d["id"] for d in f["two_persons"]] == [3]
    assert [d["id"] for d in f["unknown_correspondent"]] == [4, 5]
    assert [d["id"] for d in f["unknown_type"]] == [5]
    assert [d["id"] for d in f["no_theme"]] == [6]
    assert [d["id"] for d in f["weak_title"]] == [7]


def test_new_since_uses_the_first_document_of_each_object():
    since = dt.datetime(2026, 10, 5, tzinfo=dt.timezone.utc)
    first_added = {10: "2026-10-06T08:00:00Z", 11: "2025-01-01T00:00:00+00:00", 12: None}
    assert qr.new_since(first_added, since) == [10]


def test_stuck_queue_only_reports_documents_waiting_too_long():
    now = dt.datetime(2026, 10, 12, 7, 0, tzinfo=dt.timezone.utc)
    queued = [{"id": 1, "title": "a", "added": "2026-10-12T06:30:00Z"},
              {"id": 2, "title": "b", "added": "2026-10-12T04:00:00+00:00"}]
    assert [d["id"] for d in qr.stuck(queued, now, max_age_hours=1)] == [2]


def test_report_text_lists_sections_and_links():
    report = {
        "since": "2026-10-05", "documents": 12,
        "new_correspondents": [{"id": 1020, "name": "Stadt Bensheim", "documents": 1, "similar": ["Stadtverwaltung Bensheim"]}],
        "new_types": [], "stuck": [],
        "findings": {"no_person": [doc(2, tags=(10,))], "two_persons": [], "unknown_correspondent": [],
                     "unknown_type": [], "no_theme": [], "weak_title": []},
    }
    subject, body = qr.render(report, "https://paperless.example")
    assert "2 to check" in subject  # the likely duplicate and the document without a person
    assert "Stadt Bensheim" in body and "ähnlich: Stadtverwaltung Bensheim" in body
    assert "https://paperless.example/documents/2/details" in body
    assert "Neue Dokumenttypen" not in body  # empty sections are left out


def test_report_says_so_when_there_is_nothing_to_check():
    report = {"since": "2026-10-05", "documents": 3, "new_correspondents": [], "new_types": [], "stuck": [],
              "findings": {k: [] for k in ("no_person", "two_persons", "unknown_correspondent", "unknown_type", "no_theme", "weak_title")}}
    subject, body = qr.render(report, "https://paperless.example")
    assert "nothing to check" in subject
    assert "Keine Auffälligkeiten" in body


def _empty_report(**kw):
    r = {"since": "2026-10-05", "documents": 3, "new_correspondents": [], "new_types": [], "stuck": [],
         "findings": {k: [] for k in ("no_person", "two_persons", "unknown_correspondent", "unknown_type", "no_theme", "weak_title")}}
    r.update(kw)
    return r


def test_to_check_counts_only_what_needs_attention():
    assert qr.to_check(_empty_report()) == 0
    # A new correspondent without a similar existing one is expected, not a problem.
    assert qr.to_check(_empty_report(new_correspondents=[{"id": 1, "name": "Porkbun", "documents": 1, "similar": []}])) == 0
    assert qr.to_check(_empty_report(new_correspondents=[{"id": 1, "name": "Stadt Bensheim", "documents": 1, "similar": ["Stadtverwaltung Bensheim"]}])) == 1
    assert qr.to_check(_empty_report(new_types=[{"id": 1, "name": "Bewertung", "documents": 2}], stuck=[doc(9)])) == 2


def test_heartbeat_pings_the_configured_url_and_never_fails_the_job(monkeypatch):
    calls = []
    def fake_urlopen(url, timeout):
        calls.append(url)
        raise OSError("unreachable")
    monkeypatch.setattr(qr.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.delenv("REPORT_HEARTBEAT_URL", raising=False)
    qr.heartbeat()
    assert calls == []
    monkeypatch.setenv("REPORT_HEARTBEAT_URL", "https://hc-ping.example/abc")
    qr.heartbeat()  # must not raise although the ping fails
    assert calls == ["https://hc-ping.example/abc"]
