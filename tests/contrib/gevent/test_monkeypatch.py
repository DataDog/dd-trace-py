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
