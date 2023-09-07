import pytest

from ddtrace.appsec.iast import oce
from ddtrace.appsec.iast._ast.ast_patching import astpatch_module


try:
    from ddtrace.appsec.iast._taint_tracking import create_context
    from ddtrace.appsec.iast._taint_tracking import reset_context
except (ImportError, AttributeError):
    pytest.skip("IAST not supported for this Python version", allow_module_level=True)


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
def reset_iast_context():
    oce._enabled = True
    yield
    reset_context()
    _ = create_context()
