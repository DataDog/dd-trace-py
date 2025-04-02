import os
import subprocess
import threading

import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast._ast import iastpatch
from tests.appsec.iast.conftest import CONFIG_SERVER_PORT
from tests.utils import _build_env


def _run_python_file(*args, **kwargs):
    current_dir = os.path.dirname(__file__)
    cmd = []
    if "no_ddtracerun" not in kwargs:
        cmd += ["python", "-m", "ddtrace.commands.ddtrace_run"]

    cmd += [
        "python",
        os.path.join(current_dir, "fixtures", "integration", kwargs.get("filename", "main.py")),
    ] + list(args)
    env = _build_env(kwargs.get("env"))
    ret = subprocess.run(cmd, cwd=current_dir, env=env)

    assert ret.returncode == 0


def test_env_var_iast_enabled(capfd):
    # type: (...) -> None
    env = os.environ.copy()
    env["DD_IAST_ENABLED"] = "true"
    env["DD_TRACE_DEBUG"] = "true"
    _run_python_file(env=env)
    captured = capfd.readouterr()
    assert "iast::instrumentation::starting IAST" in captured.err
    assert "hi" in captured.out


def test_env_var_iast_disabled(monkeypatch, capfd):
    # type: (...) -> None
    env = os.environ.copy()
    env["DD_IAST_ENABLED"] = "false"
    env["DD_TRACE_DEBUG"] = "true"
    _run_python_file(env=env)
    captured = capfd.readouterr()
    assert "hi" in captured.out
    assert "iast::instrumentation::starting IAST" not in captured.err


def test_env_var_iast_unset(monkeypatch, capfd):
    # type: (...) -> None
    _run_python_file(env={"DD_TRACE_DEBUG": "true"})
    captured = capfd.readouterr()
    assert "hi" in captured.out
    assert "iast::instrumentation::starting IAST" not in captured.err


@pytest.mark.parametrize(
    "env_vars",
    [
        {"DD_IAST_ENABLED": "true", "DD_TRACE_DEBUG": "1"},
        {
            "DD_IAST_ENABLED": "true",
            "DD_TRACE_DEBUG": "1",
            "_DD_CONFIG_ENDPOINT": f"http://localhost:{CONFIG_SERVER_PORT}/",
            "_DD_CONFIG_ENDPOINT_RETRIES": "10",
        },
        {
            "DD_IAST_ENABLED": "false",
            "DD_TRACE_DEBUG": "1",
            "_DD_CONFIG_ENDPOINT": f"http://localhost:{CONFIG_SERVER_PORT}/IAST_ENABLED",
            "_DD_CONFIG_ENDPOINT_RETRIES": "10",
        },
        {
            "DD_IAST_ENABLED": "false",
            "DD_TRACE_DEBUG": "1",
            "_DD_CONFIG_ENDPOINT": f"http://localhost:{CONFIG_SERVER_PORT}/IAST_ENABLED_TIMEOUT",
            "_DD_CONFIG_ENDPOINT_TIMEOUT": "5",
        },
        {
            "DD_TRACE_DEBUG": "1",
            "_DD_CONFIG_ENDPOINT": f"http://localhost:{CONFIG_SERVER_PORT}/IAST_ENABLED",
            "_DD_CONFIG_ENDPOINT_RETRIES": "10",
        },
    ],
)
def test_env_var_iast_enabled_parametrized(capfd, configuration_endpoint, env_vars):
    env = os.environ.copy()
    for k, v in env_vars.items():
        env[k] = v
    _run_python_file(env=env)
    captured = capfd.readouterr()
    assert "hi" in captured.out
    assert "iast::instrumentation::starting IAST" in captured.err


@pytest.mark.parametrize(
    "env_vars",
    [
        {},
        {
            "DD_IAST_ENABLED": "false",
            "DD_TRACE_DEBUG": "1",
        },
        {
            "DD_IAST_ENABLED": "true",
            "DD_TRACE_DEBUG": "1",
            "_DD_CONFIG_ENDPOINT": f"http://localhost:{CONFIG_SERVER_PORT}/IAST_DISABLED",
            "_DD_CONFIG_ENDPOINT_RETRIES": "10",
        },
        {
            "DD_IAST_ENABLED": "false",
            "DD_TRACE_DEBUG": "1",
            "_DD_CONFIG_ENDPOINT": f"http://localhost:{CONFIG_SERVER_PORT}/",
            "_DD_CONFIG_ENDPOINT_RETRIES": "10",
        },
        {
            "DD_IAST_ENABLED": "false",
            "DD_TRACE_DEBUG": "1",
            "_DD_CONFIG_ENDPOINT": f"http://localhost:{CONFIG_SERVER_PORT}/IAST_ENABLED_TIMEOUT",
            "_DD_CONFIG_ENDPOINT_TIMEOUT": "2",
        },
        {
            "DD_TRACE_DEBUG": "1",
            "_DD_CONFIG_ENDPOINT": f"http://localhost:{CONFIG_SERVER_PORT}/IAST_DISABLED",
            "_DD_CONFIG_ENDPOINT_RETRIES": "10",
        },
        {
            "DD_TRACE_DEBUG": "1",
            "_DD_CONFIG_ENDPOINT": f"http://localhost:{CONFIG_SERVER_PORT}/",
            "_DD_CONFIG_ENDPOINT_RETRIES": "10",
        },
    ],
)
def test_env_var_iast_disabled_parametrized(capfd, configuration_endpoint, env_vars):
    env = os.environ.copy()
    for k, v in env_vars.items():
        env[k] = v
    _run_python_file(env=env)
    captured = capfd.readouterr()
    assert "hi" in captured.out
    assert "iast::instrumentation::starting IAST" not in captured.err


@pytest.mark.subprocess(env=dict(DD_IAST_ENABLED="True"), err=None)
def test_env_var_iast_enabled_no__native_module_warning():
    import ddtrace.appsec._iast._taint_tracking._native  # noqa: F401


@pytest.mark.xfail(reason="IAST not working with Gevent yet")
def test_env_var_iast_enabled_gevent_unload_modules_true(capfd):
    # type: (...) -> None
    env = os.environ.copy()
    env["DD_IAST_ENABLED"] = "true"
    env["DD_TRACE_DEBUG"] = "true"
    env["DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE"] = "true"
    _run_python_file(filename="main_gevent.py", env=env)
    captured = capfd.readouterr()
    assert "iast::instrumentation::starting IAST" in captured.err
    assert "hi" in captured.out


@pytest.mark.xfail(reason="IAST not working with Gevent yet")
def test_env_var_iast_enabled_gevent_unload_modules_false(capfd):
    # type: (...) -> None
    env = os.environ.copy()
    env["DD_IAST_ENABLED"] = "true"
    env["DD_TRACE_DEBUG"] = "true"
    env["DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE"] = "false"
    _run_python_file(filename="main_gevent.py", env=env)
    captured = capfd.readouterr()
    assert "iast::instrumentation::starting IAST" in captured.err
    assert "hi" in captured.out


@pytest.mark.xfail(reason="IAST not working with Gevent yet")
def test_env_var_iast_enabled_gevent_patch_all_true(capfd):
    # type: (...) -> None
    env = os.environ.copy()
    env["DD_IAST_ENABLED"] = "true"
    env["DD_TRACE_DEBUG"] = "true"
    _run_python_file(filename="main_gevent.py", env=env)
    captured = capfd.readouterr()
    assert "iast::instrumentation::starting IAST" in captured.err
    assert "hi" in captured.out


@pytest.mark.parametrize(
    "module_name,expected_result",
    (
        ("please_patch", iastpatch.ALLOWED_USER_ALLOWLIST),
        ("please_patch.do_not", iastpatch.ALLOWED_USER_ALLOWLIST),
        ("please_patch.submodule", iastpatch.ALLOWED_USER_ALLOWLIST),
        ("please_patch.do_not.but_yes", iastpatch.ALLOWED_USER_ALLOWLIST),
        ("please_patch.do_not.but_yes.sub", iastpatch.ALLOWED_USER_ALLOWLIST),
        ("also", iastpatch.ALLOWED_FIRST_PARTY_ALLOWLIST),
        ("anything", iastpatch.ALLOWED_FIRST_PARTY_ALLOWLIST),
        ("also.that", iastpatch.ALLOWED_USER_ALLOWLIST),
        ("also.that.but", iastpatch.ALLOWED_USER_ALLOWLIST),
        ("also.that.but.not", iastpatch.ALLOWED_USER_ALLOWLIST),
        ("also.that.but.not.that", iastpatch.ALLOWED_USER_ALLOWLIST),
        ("tests.appsec.iast", iastpatch.DENIED_BUILTINS_DENYLIST),
        ("tests.appsec.iast.sub", iastpatch.DENIED_BUILTINS_DENYLIST),
        ("ddtrace.allowed", iastpatch.ALLOWED_USER_ALLOWLIST),
        ("ddtrace", iastpatch.DENIED_NOT_FOUND),
        ("ddtrace.sub", iastpatch.DENIED_NOT_FOUND),
        ("hypothesis", iastpatch.DENIED_NOT_FOUND),
        ("pytest", iastpatch.DENIED_NOT_FOUND),
    ),
)
def test_env_var_iast_modules_to_patch(module_name, expected_result):
    # type: (...) -> None
    default = os.environ[IAST.PATCH_MODULES]
    try:
        os.environ[IAST.PATCH_MODULES] = IAST.SEP_MODULES.join(
            ["ddtrace.allowed.", "please_patch.", "also.that.", "please_patch.do_not.but_yes."]
        )
        os.environ[IAST.DENY_MODULES] = IAST.SEP_MODULES.join(["please_patch.do_not.", "also.that.but.not.that."])
        iastpatch.build_list_from_env(IAST.PATCH_MODULES)
        iastpatch.build_list_from_env(IAST.DENY_MODULES)

        assert iastpatch.should_iast_patch(module_name) == expected_result, module_name
    finally:
        os.environ[IAST.PATCH_MODULES] = default


def assert_configure_wrong(monkeypatch, capfd, iast_enabled, env):
    _run_python_file(iast_enabled, env=env, filename="main_configure_wrong.py", no_ddtracerun=True)
    captured = capfd.readouterr()
    assert "configuring IAST to false" in captured.out
    assert "hi" in captured.out
    assert "ImportError: IAST not enabled" not in captured.err


def assert_configure_right_disabled(monkeypatch, capfd, iast_enabled, env):
    _run_python_file(iast_enabled, env=env, filename="main_configure_right.py", no_ddtracerun=True)
    captured = capfd.readouterr()
    assert "configuring IAST to False" in captured.out
    assert "hi" in captured.out
    assert "ImportError: IAST not enabled" not in captured.err


def assert_configure_right_enabled(monkeypatch, capfd, iast_enabled, env):
    _run_python_file(iast_enabled, env=env, filename="main_configure_right.py", no_ddtracerun=True)
    captured = capfd.readouterr()
    assert "configuring IAST to True" in captured.out
    assert "hi" in captured.out
    assert "ImportError: IAST not enabled" not in captured.err


def test_env_var__configure_wrong(monkeypatch, capfd):
    # type: (...) -> None
    env = os.environ.copy()
    iast_enabled = "false"
    # Test with DD_IAST_ENABLED = "false"
    env["DD_IAST_ENABLED"] = iast_enabled
    env["DD_TRACE_DEBUG"] = "true"
    assert_configure_wrong(monkeypatch, capfd, iast_enabled, env)
    # Test with env var unset
    del env["DD_IAST_ENABLED"]
    assert_configure_wrong(monkeypatch, capfd, iast_enabled, env)


def test_env_var__configure_right(monkeypatch, capfd):
    # type: (...) -> None
    env = os.environ.copy()
    iast_enabled = "false"
    # Test with DD_IAST_ENABLED = "false"
    env["DD_IAST_ENABLED"] = iast_enabled
    env["DD_TRACE_DEBUG"] = "true"
    assert_configure_right_disabled(monkeypatch, capfd, iast_enabled, env)
    # Test with env var unset
    del env["DD_IAST_ENABLED"]
    assert_configure_right_disabled(monkeypatch, capfd, iast_enabled, env)

    iast_enabled = "true"
    # Test with DD_IAST_ENABLED = "true"
    env["DD_IAST_ENABLED"] = iast_enabled
    assert_configure_right_enabled(monkeypatch, capfd, iast_enabled, env)


@pytest.mark.parametrize("_iast_enabled", ["true", "false"])
@pytest.mark.parametrize("no_ddtracerun", [True, False])
def test_config_over_env_var(_iast_enabled, no_ddtracerun, monkeypatch, capfd):
    # Test that ``tracer.configure`` takes precedence over env var value
    env = os.environ.copy()
    env["DD_IAST_ENABLED"] = _iast_enabled
    env["DD_TRACE_DEBUG"] = "true"
    _run_python_file(_iast_enabled, env=env, filename="main_configure.py", no_ddtracerun=True, returncode=0)
    captured = capfd.readouterr()
    assert f"IAST env var: {_iast_enabled.capitalize()}" in captured.out
    assert "hi" in captured.out
    assert "ImportError: IAST not enabled" not in captured.err


@pytest.mark.parametrize(
    "module_name,expected_error",
    [
        ("", "Invalid module name"),
        ("a" * 540, "Module name too long"),
        ("invalid!module@name", "Invalid characters in module name"),
        ("module/name", "Invalid characters in module name"),
        (None, "argument 1 must be str, not None"),
    ],
)
def test_should_iast_patch_invalid_input(module_name, expected_error):
    with pytest.raises(Exception) as excinfo:
        iastpatch.should_iast_patch(module_name)
    assert str(excinfo.value) == expected_error


def test_should_iast_patch_empty_lists():
    os.environ[IAST.PATCH_MODULES] = ""
    os.environ[IAST.DENY_MODULES] = ""
    iastpatch.build_list_from_env(IAST.PATCH_MODULES)
    iastpatch.build_list_from_env(IAST.DENY_MODULES)

    assert iastpatch.should_iast_patch("some.module") == iastpatch.ALLOWED_FIRST_PARTY_ALLOWLIST


def test_should_iast_patch_max_list_size():
    large_list = ",".join([f"module{i}." for i in range(1000)])
    os.environ[IAST.PATCH_MODULES] = large_list
    iastpatch.build_list_from_env(IAST.PATCH_MODULES)
    assert iastpatch.should_iast_patch("module1") == iastpatch.ALLOWED_USER_ALLOWLIST
    assert iastpatch.should_iast_patch("module2") == iastpatch.ALLOWED_USER_ALLOWLIST
    assert iastpatch.should_iast_patch("module2000") == iastpatch.ALLOWED_FIRST_PARTY_ALLOWLIST


@pytest.mark.parametrize(
    "module_name,allowlist,denylist,expected_result",
    [
        ("module.both", "module.both.", "module.both.", iastpatch.ALLOWED_USER_ALLOWLIST),
        ("parent.child", "parent.child.", "parent.", iastpatch.ALLOWED_USER_ALLOWLIST),
        ("parent.child", "parent.", "parent.child.", iastpatch.ALLOWED_USER_ALLOWLIST),
        ("parent.child2", "parent.child.", "parent.child2.", iastpatch.DENIED_USER_DENYLIST),
        ("django.core.", "django.core.", "", iastpatch.ALLOWED_USER_ALLOWLIST),
    ],
)
def test_should_iast_patch_priority_conflicts(module_name, allowlist, denylist, expected_result):
    os.environ[IAST.PATCH_MODULES] = allowlist
    os.environ[IAST.DENY_MODULES] = denylist
    iastpatch.build_list_from_env(IAST.PATCH_MODULES)
    iastpatch.build_list_from_env(IAST.DENY_MODULES)
    assert iastpatch.should_iast_patch(module_name) == expected_result


@pytest.mark.parametrize(
    "module_name,expected_result",
    [
        ("__main__", iastpatch.ALLOWED_FIRST_PARTY_ALLOWLIST),
        ("__init__", iastpatch.ALLOWED_FIRST_PARTY_ALLOWLIST),
        ("a.b.c.d.e.f", iastpatch.ALLOWED_FIRST_PARTY_ALLOWLIST),
        ("module2.sub1", iastpatch.ALLOWED_FIRST_PARTY_ALLOWLIST),
        ("my_module._private", iastpatch.ALLOWED_FIRST_PARTY_ALLOWLIST),
    ],
)
def test_should_iast_patch_special_modules(module_name, expected_result):
    assert iastpatch.should_iast_patch(module_name) == expected_result


def test_should_iast_patch_cached_packages():
    result1 = iastpatch.should_iast_patch("new.module")
    result2 = iastpatch.should_iast_patch("new.module")

    assert result1 == result2


def test_should_iast_patch_thread_safety():
    results = []

    def check_module():
        results.append(iastpatch.should_iast_patch("concurrent.module"))

    threads = [threading.Thread(target=check_module) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(set(results)) == 1
