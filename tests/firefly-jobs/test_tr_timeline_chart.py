import json, pathlib, shutil, subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
CHART = REPO / "apps" / "firefly-importer"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="needs helm")


def render(tr):
    """The chart's TR timeline CronJobs for this trTimeline block, by name."""
    values = {"trTimeline": {"enabled": True, **tr}}
    out = subprocess.run(
        ["helm", "template", "firefly-importer", str(CHART), "--namespace", "argocd",
         "--values", str(REPO / "tests" / "alert-rules" / "values.yaml"),
         "--values", str(pathlib.Path(__file__).parent / "values.yaml"),
         "--set-json", "trTimeline=" + json.dumps(values["trTimeline"]),
         "--show-only", "templates/firefly-importer-tr-timeline-cron.yaml"],
        check=True, capture_output=True, text=True).stdout
    jobs = {}
    for doc in out.split("\n---"):
        if "kind: CronJob" in doc:
            name = doc.split("name: ", 1)[1].split("\n", 1)[0].strip()
            jobs[name] = doc
    return jobs


def env(doc, name):
    after = doc.split(f"- name: {name}\n", 1)[1]
    return after.split("value: ", 1)[1].split("\n", 1)[0].strip().strip('"')


SINGLE = {"credentialsSecret": "a-cred", "sessionClaim": "a-session",
          "cashAccount": 1, "depotAccount": 2}


def test_single_account_keeps_its_job_name():
    jobs = render(SINGLE)
    assert list(jobs) == ["firefly-importer-tr-timeline"]
    assert env(jobs["firefly-importer-tr-timeline"], "TR_CASH_ACCOUNT") == "1"


def test_each_listed_account_gets_its_own_job():
    jobs = render({**SINGLE, "accounts": [
        {"name": "second", "label": "Second", "credentialsSecret": "t-cred",
         "sessionClaim": "t-session", "cashAccount": 11, "depotAccount": 12,
         "windowDays": 0, "dryRun": True}]})
    assert sorted(jobs) == ["firefly-importer-tr-timeline", "firefly-importer-tr-timeline-second"]
    t = jobs["firefly-importer-tr-timeline-second"]
    assert env(t, "TR_CASH_ACCOUNT") == "11"
    assert env(t, "TR_DEPOT_ACCOUNT") == "12"
    assert env(t, "TR_WINDOW_DAYS") == "0"
    assert env(t, "TR_DRY_RUN") == "true"
    assert env(t, "TR_ACCOUNT_LABEL") == "Second"
    assert "claimName: t-session" in t and "name: t-cred" in t
    m = jobs["firefly-importer-tr-timeline"]
    assert env(m, "TR_WINDOW_DAYS") == "90" and env(m, "TR_DRY_RUN") == "false"
    assert "claimName: a-session" in m


def test_listed_account_inherits_the_shared_settings():
    jobs = render({**SINGLE, "windowDays": 30, "accounts": [
        {"name": "second", "credentialsSecret": "t-cred", "sessionClaim": "t-session",
         "cashAccount": 11, "depotAccount": 12}]})
    assert env(jobs["firefly-importer-tr-timeline-second"], "TR_WINDOW_DAYS") == "30"
