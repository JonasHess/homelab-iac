import importlib.util, pathlib, sys

SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "apps" / "paperlessgpt" / "scripts"

def _load(name):
    """Import a job script by path.

    The script ships inside a ConfigMap, so it is not an installable package. It must
    import with no environment set, which is why it reads its configuration in main().
    """
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
