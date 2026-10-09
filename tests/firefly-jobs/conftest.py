import importlib.util, pathlib, sys

SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "apps" / "firefly-importer" / "scripts"

def _load(name):
    """Import a job script by path.

    The scripts ship inside a ConfigMap, so they are not an installable package.
    They must import with no environment set, which is why every job reads its
    configuration inside main() rather than at module level.
    """
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
