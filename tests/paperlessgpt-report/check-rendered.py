"""Checks the rendered quality-report resources. Usage: check-rendered.py <dir> <script>"""
import json
import pathlib
import sys

import yaml

out, script = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]).read_text(encoding="utf-8")
load = lambda f: [d for d in yaml.safe_load_all((out / f).read_text(encoding="utf-8")) if d]

def fail(msg):
    sys.exit("FAIL " + msg)

if any(d["kind"] == "CronJob" for d in load("disabled.yaml")):
    fail("CronJob rendered although qualityReport.enabled is false")

docs = load("enabled.yaml")
cron = next((d for d in docs if d["kind"] == "CronJob"), None) or fail("no CronJob when enabled")
cm = next(d for d in docs if d["kind"] == "ConfigMap" and "quality_report.py" in d.get("data", {}))
if cm["data"]["quality_report.py"].rstrip("\n") != script.rstrip("\n"):
    fail("script in the ConfigMap differs from apps/paperlessgpt/scripts/quality_report.py")
compile(cm["data"]["quality_report.py"], "quality_report.py", "exec")

env = {e["name"]: e for e in cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["env"]}
themes = json.loads(env["REPORT_THEME_TAGS"]["value"])
if len(themes) < 10 or "Rechnungen & Belege" not in themes:
    fail(f"theme tags did not arrive intact: {themes}")
for key in ("MAIL_PASSWORD", "PAPERLESS_API_TOKEN"):
    if "secretKeyRef" not in env[key].get("valueFrom", {}):
        fail(f"{key} must come from a Secret")
es = next(d for d in docs if d["kind"] == "ExternalSecret" and d["spec"]["target"]["name"] == "paperlessgpt-quality-report")
container = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
if {"secretRef": {"name": "paperlessgpt-quality-report-heartbeat"}} not in container.get("envFrom", []):
    fail("heartbeat enabled but its Secret is not mounted")
if not any(d["kind"] == "ExternalSecret" and d["spec"]["target"]["name"] == "paperlessgpt-quality-report-heartbeat" for d in docs):
    fail("heartbeat enabled but no ExternalSecret for its URL")
print(f"ok  CronJob {cron['spec']['schedule']!r}, {len(themes)} themes, password from {es['spec']['data'][0]['remoteRef']['key']}")
