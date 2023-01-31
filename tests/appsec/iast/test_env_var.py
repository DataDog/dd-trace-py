#!/usr/bin/env python3
import os
import subprocess


def _run_python_file(*args):
    current_dir = os.path.dirname(__file__)
    cmd = ["ddtrace-run", "python", os.path.join(current_dir, "fixtures", "integration", "print_str.py")] + list(args)
    ret = subprocess.run(cmd)
    assert ret.returncode == 0


def test_env_var_iast_enabled(monkeypatch, capfd):
    # type: (...) -> None
    monkeypatch.setenv("DD_IAST_ENABLED", "true")
    _run_python_file()
    captured = capfd.readouterr()
    assert captured.out == "hi\n"


def test_env_var_iast_disabled(monkeypatch, capfd):
    # type: (...) -> None
    monkeypatch.setenv("DD_IAST_ENABLED", "false")
    _run_python_file()
    captured = capfd.readouterr()
    assert captured.out == "hi\n"


def test_env_var_iast_unset(monkeypatch, capfd):
    # type: (...) -> None
    _run_python_file()
    captured = capfd.readouterr()
    assert captured.out == "hi\n"
