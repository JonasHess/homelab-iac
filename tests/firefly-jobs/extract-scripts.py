"""Pull every *.py ConfigMap value out of a rendered chart and write it to disk.

Refuses to write an implausibly short script. A silently empty extraction is the
failure mode worth guarding: it makes py_compile and the unit tests pass on nothing.
"""
import pathlib, sys

MIN_LINES = 20

src, outdir = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
lines = src.read_text(encoding="utf-8").split("\n")

found = {}
for i, line in enumerate(lines):
    stripped = line.strip()
    if not (stripped.endswith(".py: |") and line.startswith("  ")):
        continue
    name = stripped[:-len(": |")]
    body, j = [], i + 1
    while j < len(lines) and (lines[j].startswith("    ") or not lines[j].strip()):
        body.append(lines[j][4:] if lines[j].strip() else "")
        j += 1
    found[name] = body

if not found:
    sys.exit("no *.py ConfigMap entries found in the rendered chart")

for name, body in sorted(found.items()):
    if len(body) < MIN_LINES:
        sys.exit(f"{name} rendered to only {len(body)} lines - extraction or template is broken")
    (outdir / name).write_text("\n".join(body) + "\n", encoding="utf-8")
    print(f"extracted {name}: {len(body)} lines")
