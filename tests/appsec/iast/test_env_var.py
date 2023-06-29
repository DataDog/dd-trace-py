#!/usr/bin/env python3
import os
import subprocess

import pytest
from six import PY2

from ddtrace.appsec.iast._util import _is_python_version_supported


def _run_python_file(*args, **kwargs):
    current_dir = os.path.dirname(__file__)
    cmd = [
        "ddtrace-run",
        "-d",
        "python",
        os.path.join(current_dir, "fixtures", "integration", kwargs.get("filename", "main.py")),
    ] + list(args)
    if "env" in kwargs:
        ret = subprocess.run(cmd, cwd=current_dir, env=kwargs["env"])
    else:
        ret = subprocess.run(cmd, cwd=current_dir)
    assert ret.returncode == 0


@pytest.mark.skipif(not _is_python_version_supported(), reason="IAST compatible versions")
def test_env_var_iast_enabled(capfd):
    # type: (...) -> None
    env = os.environ.copy()
    env["DD_IAST_ENABLED"] = "true"
    _run_python_file(env=env)
    captured = capfd.readouterr()
    assert "IAST enabled" in captured.err
    assert "hi" in captured.out


@pytest.mark.skipif(PY2, reason="Not testing Python 2")
def test_env_var_iast_disabled(monkeypatch, capfd):
    # type: (...) -> None
    env = os.environ.copy()
    env["DD_IAST_ENABLED"] = "false"
    _run_python_file(env=env)
    captured = capfd.readouterr()
    assert "hi" in captured.out
    assert "IAST enabled" not in captured.err


@pytest.mark.skipif(PY2, reason="Not testing Python 2")
def test_env_var_iast_unset(monkeypatch, capfd):
    # type: (...) -> None
    _run_python_file()
    captured = capfd.readouterr()
    assert "hi" in captured.out
    assert "IAST enabled" not in captured.err


@pytest.mark.skipif(not _is_python_version_supported(), reason="IAST compatible versions")
@pytest.mark.xfail(reason="IAST now working with Gevent yet")
def test_env_var_iast_enabled_gevent_unload_modules_true(capfd):
    # type: (...) -> None
    env = os.environ.copy()
    env["DD_IAST_ENABLED"] = "true"
    env["DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE"] = "true"
    _run_python_file(filename="main_gevent.py", env=env)
    captured = capfd.readouterr()
    assert "IAST enabled" in captured.err
    assert "hi" in captured.out


@pytest.mark.skipif(not _is_python_version_supported(), reason="IAST compatible versions")
@pytest.mark.xfail(reason="IAST now working with Gevent yet")
def test_env_var_iast_enabled_gevent_unload_modules_false(capfd):
    # type: (...) -> None
    env = os.environ.copy()
    env["DD_IAST_ENABLED"] = "true"
    env["DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE"] = "false"
    _run_python_file(filename="main_gevent.py", env=env)
    captured = capfd.readouterr()
    assert "IAST enabled" in captured.err
    assert "hi" in captured.out


@pytest.mark.skipif(not _is_python_version_supported(), reason="IAST compatible versions")
@pytest.mark.xfail(reason="IAST now working with Gevent yet")
def test_env_var_iast_enabled_gevent_patch_all_true(capfd):
    # type: (...) -> None
    env = os.environ.copy()
    env["DD_IAST_ENABLED"] = "true"
    env["DD_GEVENT_PATCH_ALL"] = "true"
    _run_python_file(filename="main_gevent.py", env=env)
    captured = capfd.readouterr()
    assert "IAST enabled" in captured.err
    assert "hi" in captured.out


@pytest.mark.skipif(not _is_python_version_supported(), reason="IAST compatible versions")
def test_A_env_var_iast_modules_to_patch(capfd):
    # type: (...) -> None
    import gc
    import sys

    from ddtrace.appsec._constants import IAST

    if "ddtrace.appsec.iast._ast.ast_patching" in sys.modules:
        del sys.modules["ddtrace.appsec.iast._ast.ast_patching"]
        gc.collect()

    os.environ[IAST.PATCH_MODULES] = IAST.SEP_MODULES.join(
        ["please_patch", "also.that", "ddtrace", "please_patch.do_not.but_yes"]
    )
    os.environ[IAST.DENY_MODULES] = IAST.SEP_MODULES.join(["please_patch.do_not", "also.that.but.not.that"])
    import ddtrace.appsec.iast._ast.ast_patching as ap

    for module_name in [
        "please_patch",
        "please_patch.submodule",
        "please_patch.do_not.but_yes",
        "please_patch.do_not.but_yes.sub",
        "also",
        "anything",
        "also.that",
        "also.that.but",
        "also.that.but.not",
        "tests.appsec.iast",
        "tests.appsec.iast.sub",
    ]:
        assert ap._should_iast_patch(module_name), module_name

    for module_name in [
        "ddtrace",
        "ddtrace.sub",
        "hypothesis",
        "pytest",
        "please_patch.do_not",
        "please_patch.do_not.any",
        "also.that.but.not.that",
        "also.that.but.not.that.never",
    ]:
        assert not ap._should_iast_patch(module_name), module_name
