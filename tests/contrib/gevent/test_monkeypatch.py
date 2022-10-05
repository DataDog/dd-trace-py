import pytest


@pytest.mark.subprocess
def test_gevent_auto_patching():
    import ddtrace

    # Disable tracing sqlite3 as it is used by coverage
    ddtrace.patch_all(sqlite3=False)
    # Patch on import
    import gevent  # noqa

    from ddtrace.contrib.gevent import GeventContextProvider

    assert isinstance(ddtrace.tracer.context_provider, GeventContextProvider)


def test_gevent_ddtrace_run_auto_patching(ddtrace_run_python_code_in_subprocess):
    code = """
import gevent  # Patch on import
import ddtrace  # ddtrace-run, No need to call patch_all()
from ddtrace.contrib.gevent import GeventContextProvider


assert isinstance(ddtrace.tracer.context_provider, GeventContextProvider)
"""

    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code)
    assert status == 0, err
    assert out == b""
