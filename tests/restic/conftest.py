import importlib.util, logging, pathlib, sys
from types import SimpleNamespace

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "apps" / "restic" / "assets" / "restic-operations.py"


def _load():
    """Import the script by path: its file name is not a valid module name."""
    spec = importlib.util.spec_from_file_location("restic_operations", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["restic_operations"] = mod
    spec.loader.exec_module(mod)
    return mod


restic_operations = _load()


class FakeCluster:
    """Stands in for the Kubernetes API: ResticBackups, PVCs and hostPath PVs."""

    def __init__(self):
        self.crds = []
        self.pvcs = {}  # (namespace, name) -> bound PV name, or None when unbound
        self.pvs = {}   # PV name -> hostPath

    def add_backup(self, name, host_path, bound=True):
        pvc = f"{name}-pvc"
        self.crds.append({"metadata": {"name": name}, "spec": {"namespace": "argocd", "pvcName": pvc}})
        self.pvcs[("argocd", pvc)] = f"{name}-pv" if bound else None
        self.pvs[f"{name}-pv"] = str(host_path)

    # CustomObjectsApi
    def list_cluster_custom_object(self, group, version, plural, label_selector):
        return {"items": self.crds}

    # CoreV1Api
    def read_namespaced_persistent_volume_claim(self, name, namespace):
        return SimpleNamespace(spec=SimpleNamespace(volume_name=self.pvcs[(namespace, name)]))

    def read_persistent_volume(self, name):
        return SimpleNamespace(spec=SimpleNamespace(host_path=SimpleNamespace(path=self.pvs[name])))


@pytest.fixture
def cluster():
    return FakeCluster()


@pytest.fixture
def ops(cluster, monkeypatch):
    """A ResticOperations wired to the fake cluster, with every restic call succeeding."""
    o = restic_operations.ResticOperations.__new__(restic_operations.ResticOperations)
    o.logger = logging.getLogger("restic-test")
    o.custom_api = cluster
    o.core_api = cluster
    o.temp_dir = None
    o.restic_calls = []

    def fake_restic(args, timeout=3600, stream_output=False):
        o.restic_calls.append(args)
        return True, "[]", ""

    monkeypatch.setattr(o, "run_restic_command", fake_restic)
    return o
