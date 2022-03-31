import os
import subprocess

import gevent
import pytest


@pytest.mark.skipif(
    not (gevent.version_info.major >= 1 and gevent.version_info.minor >= 3), reason="gevent 1.3 or later is required"
)
def test_gevent_warning(monkeypatch):
    subp = subprocess.Popen(
        ("python", os.path.join(os.path.dirname(__file__), "wrong_program_gevent.py")),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
    )
    assert subp.wait() == 0
    assert subp.stdout.read() == b""
    assert b"RuntimeWarning: Loading ddtrace before using gevent monkey patching" in subp.stderr.read()


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
