import pathlib, re, shutil, subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="needs helm")


def rendered_docs():
    out = subprocess.run(
        ["helm", "template", "firefly-importer", str(REPO / "apps" / "firefly-importer"),
         "--namespace", "argocd",
         "--values", str(REPO / "tests" / "alert-rules" / "values.yaml"),
         "--values", str(pathlib.Path(__file__).parent / "values.yaml")],
        check=True, capture_output=True, text=True).stdout
    return out.split("\n---")


def test_no_job_pod_joins_the_web_service():
    # A pod carrying the Service's selector label receives the web UI's traffic while it
    # runs, and a job pod answers none of it.
    docs = rendered_docs()
    selectors = {m for d in docs if "kind: Service\n" in d
                 for m in re.findall(r"selector:\n\s+app: (\S+)", d)}
    assert selectors
    jobs = [d for d in docs if "kind: CronJob" in d]
    assert jobs
    for d in jobs:
        name = re.search(r"^  name: (\S+)", d, re.M).group(1)
        labels = set(re.findall(r"^\s+app: (\S+)", d, re.M))
        assert not labels & selectors, f"{name} carries the Service label {labels & selectors}"
