"""Weekly quality report for documents tagged by paperless-gpt.

paperless-gpt files every new document on its own. Its typical mistakes are quiet ones:
a near-duplicate correspondent, a document without a person or theme tag, a document
stuck in the queue after an LLM error. This job lists exactly those for the documents
added in the last REPORT_DAYS days and mails the list, so they can be fixed while the
week is still fresh. It only reads from Paperless.

It looks at documents *added* in the period. A correspondent or type that paperless-gpt
creates while re-processing an older document is therefore not reported as new; that
only happens in deliberate bulk re-runs, which are reviewed when they are made.
"""
import datetime as dt
import difflib
import html
import json
import os
import re
import smtplib
import ssl
import urllib.parse
import urllib.request
from email.message import EmailMessage

# Words that do not tell two organisations apart: legal forms and the administrative
# prefixes paperless-gpt is told to normalise ("Stadtverwaltung X" -> "Stadt X").
GENERIC = {
    "gmbh", "ag", "se", "kg", "mbh", "co", "ltd", "limited", "inc", "ev", "e", "v", "ug", "ohg", "kgaa",
    "deutschland", "germany", "stadt", "stadtverwaltung", "magistrat", "der", "die", "das", "und",
}
# Two names whose remaining words are this similar are flagged as a possible duplicate.
# High on purpose: the report should point at likely duplicates, not at every pair of
# names sharing a few letters.
SIMILARITY = 0.9


def _core(name):
    words = re.findall(r"[a-z0-9äöüß]+", html.unescape(name).lower())
    return [w for w in words if w not in GENERIC]


def similar(name, existing):
    """Existing names that are probably the same organisation as name."""
    core = _core(name)
    hits = []
    for other in existing:
        if other == name:
            continue
        other_core = _core(other)
        if not core or not other_core:
            continue
        if set(core) == set(other_core) or difflib.SequenceMatcher(None, " ".join(core), " ".join(other_core)).ratio() >= SIMILARITY:
            hits.append(other)
    return hits


def _parse(ts):
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None


def new_since(first_added, since):
    """Ids whose first document was added at or after since, i.e. created this period."""
    return sorted(i for i, ts in first_added.items() if ts and _parse(ts) >= since)


def stuck(queued, now, max_age_hours):
    """Queued documents that paperless-gpt should have processed by now."""
    limit = now - dt.timedelta(hours=max_age_hours)
    return [d for d in queued if _parse(d["added"]) < limit]


def findings(docs, tag_names, correspondent_names, type_names, themes, persons=("Michael", "Tatjana", "Toni")):
    """Documents with a gap paperless-gpt is expected to fill, grouped by kind."""
    out = {k: [] for k in ("no_person", "two_persons", "unknown_correspondent", "unknown_type", "no_theme", "weak_title")}
    for d in docs:
        tags = {tag_names.get(t) for t in d["tags"]}
        if not tags & set(persons):
            out["no_person"].append(d)
        if {"Michael", "Tatjana"} <= tags:
            out["two_persons"].append(d)
        if not d["correspondent"] or correspondent_names.get(d["correspondent"]) == "Unbekannt":
            out["unknown_correspondent"].append(d)
        if not d["document_type"] or type_names.get(d["document_type"]) == "Unbekannt":
            out["unknown_type"].append(d)
        if not tags & set(themes):
            out["no_theme"].append(d)
        if d["title"] == "Kein Titel gefunden" or d["title"].startswith("ERROR") or len(d["title"]) < 8:
            out["weak_title"].append(d)
    return out


SECTIONS = [
    ("no_person", "Ohne Personen-Tag"),
    ("two_persons", "Michael und Tatjana (nur richtig, wenn an beide gerichtet)"),
    ("unknown_correspondent", "Korrespondent fehlt oder Unbekannt"),
    ("unknown_type", "Dokumenttyp fehlt oder Unbekannt"),
    ("no_theme", "Ohne Themen-Tag"),
    ("weak_title", "Schwacher Titel"),
]


def render(report, base_url):
    """Subject and plain-text body of the report mail."""
    link = lambda d: f"{base_url}/documents/{d['id']}/details"
    lines, count = [], 0
    if report["new_correspondents"]:
        lines.append("Neue Korrespondenten:")
        for c in report["new_correspondents"]:
            hint = f"  (ähnlich: {', '.join(c['similar'])})" if c["similar"] else ""
            lines.append(f"  - {c['name']} ({c['documents']} Dok.){hint}")
            count += 1 if c["similar"] else 0
        lines.append("")
    if report["new_types"]:
        lines.append("Neue Dokumenttypen:")
        lines += [f"  - {t['name']} ({t['documents']} Dok.)" for t in report["new_types"]]
        count += len(report["new_types"])
        lines.append("")
    if report["stuck"]:
        lines.append("Hängen in der paperless-gpt-Warteschlange:")
        lines += [f"  - {d['title']}  {link(d)}" for d in report["stuck"]]
        count += len(report["stuck"])
        lines.append("")
    for key, heading in SECTIONS:
        docs = report["findings"][key]
        if docs:
            lines.append(f"{heading}:")
            lines += [f"  - {d['title']}  {link(d)}" for d in docs]
            count += len(docs)
            lines.append("")
    head = f"Dokumente seit {report['since']}: {report['documents']}"
    if not count:
        lines = ["Keine Auffälligkeiten."] + ([""] + lines if lines else [])
    subject = f"Paperless quality report since {report['since']}: " + (f"{count} to check" if count else "nothing to check")
    return subject, "\n".join([head, ""] + lines).rstrip() + "\n"


def main():
    base = os.environ["PAPERLESS_BASE_URL"].rstrip("/")
    public = os.environ.get("REPORT_PUBLIC_URL", base).rstrip("/")
    token = os.environ["PAPERLESS_API_TOKEN"]
    days = int(os.environ.get("REPORT_DAYS", "7"))
    max_age = int(os.environ.get("REPORT_QUEUE_MAX_AGE_HOURS", "1"))
    themes = json.loads(os.environ["REPORT_THEME_TAGS"])
    auto_tag = os.environ.get("PAPERLESS_AUTO_TAG", "paperless-gpt-auto")

    def get(path):
        req = urllib.request.Request(base + path, headers={"Authorization": "Token " + token, "Accept": "application/json; version=9"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)

    def all_of(path):
        items, url = [], path
        while url:
            page = get(url)
            items += page["results"]
            # "next" is an absolute URL built from the request's Host; keep only the path.
            url = "/api" + page["next"].split("/api", 1)[1] if page["next"] else None
        return items

    now = dt.datetime.now(dt.timezone.utc)
    since = now - dt.timedelta(days=days)
    tag_names = {t["id"]: t["name"] for t in all_of("/api/tags/?page_size=500")}
    correspondents = {c["id"]: c for c in all_of("/api/correspondents/?page_size=1000")}
    types = {t["id"]: t for t in all_of("/api/document_types/?page_size=1000")}
    fields = "id,title,correspondent,document_type,tags,added"
    docs = all_of(f"/api/documents/?added__date__gte={since.date().isoformat()}&page_size=500&fields={fields}")

    def first_added(field, ids):
        out = {}
        for i in ids:
            r = get(f"/api/documents/?{field}__id={i}&ordering=added&page_size=1&fields=added")["results"]
            out[i] = r[0]["added"] if r else None
        return out

    new_c = new_since(first_added("correspondent", {d["correspondent"] for d in docs if d["correspondent"]}), since)
    new_t = new_since(first_added("document_type", {d["document_type"] for d in docs if d["document_type"]}), since)
    names = [c["name"] for c in correspondents.values()]
    auto_id = next((i for i, n in tag_names.items() if n == auto_tag), None)
    queued = all_of(f"/api/documents/?tags__id__all={auto_id}&page_size=500&fields=id,title,added") if auto_id else []

    report = {
        "since": since.date().isoformat(),
        "documents": len(docs),
        "new_correspondents": [{"id": i, "name": correspondents[i]["name"], "documents": correspondents[i]["document_count"],
                                "similar": similar(correspondents[i]["name"], names)} for i in new_c],
        "new_types": [{"id": i, "name": types[i]["name"], "documents": types[i]["document_count"]} for i in new_t],
        "stuck": stuck(queued, now, max_age),
        "findings": findings(docs, tag_names, {k: v["name"] for k, v in correspondents.items()}, {k: v["name"] for k, v in types.items()}, themes),
    }
    subject, body = render(report, public)
    print(subject)
    print(body)
    if os.environ.get("REPORT_DRY_RUN") == "1":
        print("dry run - no mail sent")
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{os.environ.get('MAIL_FROM_NAME', 'Paperless')} <{os.environ['MAIL_FROM_ADDRESS']}>"
    msg["To"] = os.environ["MAIL_DESTINATION"]
    msg.set_content(body)
    with smtplib.SMTP(os.environ["MAIL_HOST"], int(os.environ.get("MAIL_PORT", "587")), timeout=30) as s:
        if os.environ.get("MAIL_ENCRYPTION", "tls") == "tls":
            s.starttls(context=ssl.create_default_context())
        s.login(os.environ["MAIL_USERNAME"], os.environ["MAIL_PASSWORD"])
        s.send_message(msg)
    print("mail sent to", os.environ["MAIL_DESTINATION"])


if __name__ == "__main__":
    main()
