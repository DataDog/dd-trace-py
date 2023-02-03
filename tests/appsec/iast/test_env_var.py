#!/usr/bin/env python3
import os
import subprocess
import sys

import pytest


def _run_python_file(*args, **kwargs):
    current_dir = os.path.dirname(__file__)
    cmd = [
        "ddtrace-run",
        "-d",
        "python",
        os.path.join(current_dir, "fixtures", "integration", "main.py"),
    ] + list(args)
    if "env" in kwargs:
        ret = subprocess.run(cmd, cwd=current_dir, env=kwargs["env"])
    else:
        ret = subprocess.run(cmd, cwd=current_dir)
    assert ret.returncode == 0


@pytest.mark.skipif((3, 6, 0) < sys.version_info < (3, 11, 0), reason="IAST compatible versions")
def test_env_var_iast_enabled(capfd):
    # type: (...) -> None
    env = os.environ.copy()
    env["DD_IAST_ENABLED"] = "true"
    _run_python_file(env=env)
    captured = capfd.readouterr()
    assert "DEBUG:ddtrace.internal.module:IAST enabled" in captured.err
    assert "IAST enabled" not in captured.out
    assert "hi" in captured.out


@pytest.mark.skipif(sys.version_info[0] < 3, reason="Not testing Python 2")
def test_env_var_iast_disabled(monkeypatch, capfd):
    # type: (...) -> None
    env = os.environ.copy()
    env["DD_IAST_ENABLED"] = "false"
    _run_python_file(env=env)
    captured = capfd.readouterr()
    assert "hi" in captured.out
    assert "DEBUG:ddtrace.internal.module:IAST enabled" not in captured.err
    assert "IAST enabled" not in captured.out


@pytest.mark.skipif(sys.version_info[0] < 3, reason="Not testing Python 2")
def test_env_var_iast_unset(monkeypatch, capfd):
    # type: (...) -> None
    _run_python_file()
    captured = capfd.readouterr()
    assert "hi" in captured.out
    assert "DEBUG:ddtrace.internal.module:IAST enabled" not in captured.err
    assert "IAST enabled" not in captured.out
