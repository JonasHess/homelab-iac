"""Checks that Paperless accepts every in-cluster host its clients call it by.

Once PAPERLESS_URL is set, Paperless allows only that URL's host, localhost and
PAPERLESS_ALLOWED_HOSTS; any other Host header gets a 400 (DisallowedHost), which
clients like paperless-gpt only log and back off from. Callers in other namespaces
use the service FQDN through the environment overlays, which are not in this repo,
so that name is required even when no rendered chart uses it.

Usage: check-paperless-hosts.py <rendered dir>
"""
import pathlib
import re
import sys

import yaml

out = pathlib.Path(sys.argv[1])
SERVICE = "paperlessngx-service"

config = next(
    d for d in yaml.safe_load_all((out / "paperlessngx.yaml").read_text(encoding="utf-8"))
    if d and d["kind"] == "ConfigMap" and d["metadata"]["name"] == "paperlessngx-config"
)
namespace = config["metadata"]["namespace"]
allowed = {h.strip() for h in config["data"].get("PAPERLESS_ALLOWED_HOSTS", "").split(",") if h.strip()}

callers = {f"{SERVICE}.{namespace}.svc.cluster.local": "environment overlays"}
for f in sorted(out.glob("*.yaml")):
    for host in re.findall(rf"https?://({SERVICE}[\w.-]*)", f.read_text(encoding="utf-8")):
        callers.setdefault(host, f.name)

missing = {h: src for h, src in callers.items() if h not in allowed}
for host, src in missing.items():
    print(f"FAIL {host} (used by {src}) is not in PAPERLESS_ALLOWED_HOSTS", file=sys.stderr)
if missing:
    sys.exit(1)
print(f"ok  Paperless allows all {len(callers)} in-cluster hosts: {', '.join(sorted(callers))}")
