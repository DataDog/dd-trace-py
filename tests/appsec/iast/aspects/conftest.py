import importlib
import types

import pytest

from ddtrace.appsec._iast._ast.ast_patching import _should_iast_patch
from ddtrace.appsec._iast._ast.ast_patching import astpatch_module
from tests.appsec.iast.conftest import _end_iast_context_and_oce
from tests.appsec.iast.conftest import _start_iast_context_and_oce
from tests.utils import override_global_config


class IastTestException(Exception):
    pass


def _iast_patched_module_and_patched_source(module_name, new_module_object=False):
    module = importlib.import_module(module_name)
    module_path, patched_source = astpatch_module(module)
    compiled_code = compile(patched_source, module_path, "exec")
    module_changed = types.ModuleType(module_name) if new_module_object else module
    exec(compiled_code, module_changed.__dict__)
    return module_changed, patched_source


def _iast_patched_module(module_name, new_module_object=False):
    if _should_iast_patch(module_name):
        module, _ = _iast_patched_module_and_patched_source(module_name, new_module_object)
    else:
        raise IastTestException(f"IAST Test Error: module {module_name} was excluded")
    return module


@pytest.fixture(autouse=True)
def iast_create_context():
    with override_global_config(dict(_iast_enabled=True, _deduplication_enabled=False, _iast_request_sampling=100)):
        _start_iast_context_and_oce()
        yield
        _end_iast_context_and_oce()
