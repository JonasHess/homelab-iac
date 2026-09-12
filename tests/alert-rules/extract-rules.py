"""Turn `helm template` output into a Prometheus rule file.

A PrometheusRule's .spec is already exactly Prometheus' rule-file schema, so the job is
to find those documents and re-emit their spec block dedented by one level. Deliberately
stdlib-only: this runs in CI, and a YAML parser would be a dependency to install and
keep working for a transformation this mechanical.
"""
import sys

out_path, chart = sys.argv[1], sys.argv[2]
docs = sys.stdin.read().split('\n---\n')
groups = []

for doc in docs:
    lines = doc.split('\n')
    if not any(l.strip() == 'kind: PrometheusRule' for l in lines):
        continue
    try:
        start = next(i for i, l in enumerate(lines) if l == 'spec:')
    except StopIteration:
        sys.exit(f'{chart}: PrometheusRule without a top-level spec:')
    body = []
    for l in lines[start + 1:]:
        if l and not l.startswith('  '):   # dedent back to column 0 ends the spec block
            break
        body.append(l[2:] if l.startswith('  ') else l)
    # Drop the "groups:" header; the caller re-adds one so several documents can merge.
    if body and body[0].strip() == 'groups:':
        body = body[1:]
    groups.extend(body)

if not groups:
    sys.exit(f'no PrometheusRule rendered for {chart}')

with open(out_path, 'w') as fh:
    fh.write('groups:\n' + '\n'.join(groups).rstrip() + '\n')

print('  rendered %s: %d rule(s)' % (chart, sum(1 for l in groups if l.strip().startswith('- alert:'))))
