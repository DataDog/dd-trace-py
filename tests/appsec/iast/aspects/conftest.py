import importlib

import pytest

from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._ast.ast_patching import astpatch_module


def _iast_patched_module_and_patched_source(module_name):
    module = importlib.import_module(module_name)
    module_path, patched_source = astpatch_module(module)

    compiled_code = compile(patched_source, module_path, "exec")
    exec(compiled_code, module.__dict__)
    return module, patched_source


def _iast_patched_module(module_name):
    module, patched_source = _iast_patched_module_and_patched_source(module_name)
    return module


@pytest.fixture(autouse=True, scope="module")
def _enable_oce():
    oce._enabled = True
    yield
    oce._enabled = False
