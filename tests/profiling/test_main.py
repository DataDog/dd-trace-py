# -*- encoding: utf-8 -*-
import os
import sys

import pytest

from tests.utils import call_program


@pytest.mark.skipif(sys.platform == "win32", reason="fork only available on Unix")
@pytest.mark.skipif(not os.getenv("DD_PROFILE_TEST_GEVENT", False), reason="Not testing gevent")
def test_fork_gevent(monkeypatch):
    monkeypatch.setenv("DD_PROFILING_API_TIMEOUT", "0.1")
    stdout, stderr, exitcode, pid = call_program("python", os.path.join(os.path.dirname(__file__), "gevent_fork.py"))
    assert exitcode == 0


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(DD_PROFILING_ENABLED="1"),
    err=lambda _: "RuntimeError: the memalloc module is already started" not in _,
)
def test_memalloc_no_init_error_on_fork():
    import os

    pid = os.fork()
    if not pid:
        exit(0)
    os.waitpid(pid, 0)
