import importlib.util, pathlib, sys

SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "teddycloud"
sys.path.insert(0, str(SCRIPTS))

def _load(name):
    """Import a script by path; the scripts are run directly, not installed as a package."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
