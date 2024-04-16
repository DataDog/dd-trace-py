import logging

import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._ast.ast_patching import astpatch_module
from tests.utils import override_env


def _iast_patched_module(module_name):
    module = __import__(module_name, fromlist=[None])
    module_path, patched_source = astpatch_module(module)

    compiled_code = compile(patched_source, module_path, "exec")
    exec(compiled_code, module.__dict__)
    return module


@pytest.fixture(autouse=True, scope="module")
def _enable_oce():
    oce._enabled = True
    yield
    oce._enabled = False


@pytest.fixture(autouse=True)
def check_native_code_exception_in_each_python_aspect_test(caplog):
    with override_env({IAST.ENV_DEBUG: "true"}), caplog.at_level(logging.DEBUG):
        yield
    assert not any("[IAST] " in record.message for record in caplog.records), [
        record.message for record in caplog.records
    ]
