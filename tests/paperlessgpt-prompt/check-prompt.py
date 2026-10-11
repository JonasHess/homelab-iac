"""Checks the rendered paperless-gpt prompt ConfigMap and its mount.

Usage: check-prompt.py <rendered.yaml> with-n8n|without-n8n
"""
import json
import sys

import yaml

N8N_FIELDS = ['"summary"', '"urgency"', '"action_required"', '"action_description"']
N8N_SECTIONS = ["# Feld: summary", "# Feld: urgency", "# Feld: action_required", "# Feld: action_description"]
PLACEHOLDERS = [
    "{{ .PromptPreamble }}",
    "{{.AvailableCorrespondents | join \", \"}}",
    "{{.AvailableDocumentTypes | join \", \"}}",
    "{{.AvailableTags | join \", \"}}",
    "{{.TagsExplanation}}",
    "{{.Content}}",
]


def fail(msg):
    print(f"FAIL {variant}: {msg}")
    sys.exit(1)


path, variant = sys.argv[1], sys.argv[2]
docs = [d for d in yaml.safe_load_all(open(path, encoding="utf-8")) if d]

configmaps = [d for d in docs if d["kind"] == "ConfigMap" and "json_prompt.tmpl" in (d.get("data") or {})]
if len(configmaps) != 1:
    fail(f"expected one ConfigMap with json_prompt.tmpl, found {len(configmaps)}")
prompt = configmaps[0]["data"]["json_prompt.tmpl"]
if len(prompt) < 2000:
    fail(f"prompt is suspiciously short ({len(prompt)} chars)")

if "@@" in prompt:
    fail("an unreplaced @@ marker is left in the prompt")
for p in PLACEHOLDERS:
    if p not in prompt:
        fail(f"placeholder {p} is missing - Helm may have evaluated it")

want_n8n = variant == "with-n8n"
for marker in N8N_FIELDS + N8N_SECTIONS:
    if (marker in prompt) != want_n8n:
        fail(f"{marker} {'missing' if want_n8n else 'present'}")

schema = prompt.split("```json", 1)[1].split("```", 1)[0]
try:
    json.loads(schema)
except ValueError as e:
    fail(f"JSON schema example is not valid JSON: {e}")

deployment = next(d for d in docs if d["kind"] == "Deployment")
container = deployment["spec"]["template"]["spec"]["containers"][0]
mounts = {m["mountPath"]: m["name"] for m in container.get("volumeMounts", [])}
volume_name = mounts.get("/app/config/prompts")
if not volume_name:
    fail("nothing is mounted at /app/config/prompts")
volumes = {v["name"]: v for v in deployment["spec"]["template"]["spec"]["volumes"]}
if volumes[volume_name].get("configMap", {}).get("name") != configmaps[0]["metadata"]["name"]:
    fail("/app/config/prompts is not the prompt ConfigMap")

print(f"ok  {variant}  {len(prompt)} chars")
