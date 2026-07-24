import importlib


def load_dependency():
    module = importlib.import_module("tests.coverage.included_path.importlib_dep")
    return module.VALUE
