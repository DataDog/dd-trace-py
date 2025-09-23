import os
import sys
from unittest.mock import patch

import pytest


# Python interpreters for parametrized testing
PYTHON_INTERPRETERS = [
    "/usr/bin/python",
    "/usr/bin/python3",
    "/usr/bin/python3.8",
    "/usr/bin/python3.9",
    "/usr/bin/python3.10",
    "/usr/bin/python3.11",
    "/usr/bin/python3.12",
    "/usr/local/bin/python",
    "/usr/local/bin/python3",
    "/opt/python/bin/python3.10",
    "/home/user/.pyenv/versions/3.11.0/bin/python",
    "python",
    "python3",
    "python3.10",
    "./python",
    "../bin/python3",
]


@pytest.fixture
def mock_sitecustomize():
    lib_injection_path = os.path.join(os.path.dirname(__file__), "../../lib-injection/sources")
    if lib_injection_path not in sys.path:
        sys.path.insert(0, lib_injection_path)

    import sitecustomize

    sitecustomize.EXECUTABLES_DENY_LIST = sitecustomize.build_denied_executables()
    sitecustomize.EXECUTABLE_MODULES_DENY_LIST = sitecustomize.build_denied_executable_modules()

    return sitecustomize


@pytest.mark.parametrize("python_exe", PYTHON_INTERPRETERS)
def test_python_module_denylist_denied_basic(mock_sitecustomize, python_exe):
    assert "py_compile" in mock_sitecustomize.EXECUTABLE_MODULES_DENY_LIST, "py_compile should be in modules deny list"

    with patch.object(sys, "argv", [python_exe, "-m", "py_compile", "test.py"]):
        result = mock_sitecustomize.get_first_incompatible_sysarg()
        assert result == "-m py_compile", f"Expected '-m py_compile' for {python_exe}, got '{result}'"


@pytest.mark.parametrize(
    "python_exe, argv_pattern, description",
    [
        (PYTHON_INTERPRETERS[1], ["-v", "-m", "py_compile", "test.py"], "python -v -m py_compile"),
        (PYTHON_INTERPRETERS[8], ["-u", "-m", "py_compile"], "python -u -m py_compile"),
        (PYTHON_INTERPRETERS[12], ["-O", "-v", "-m", "py_compile"], "python -O -v -m py_compile"),
        (PYTHON_INTERPRETERS[1], ["-W", "ignore", "-m", "py_compile"], "python -W ignore -m py_compile"),
        (PYTHON_INTERPRETERS[8], ["-u", "-v", "-m", "py_compile"], "python -u -v -m py_compile"),
        (PYTHON_INTERPRETERS[12], ["-O", "-m", "py_compile", "file.py"], "python -O -m py_compile"),
    ],
)
def test_python_module_denylist_denied_with_flags(mock_sitecustomize, python_exe, argv_pattern, description):
    assert "py_compile" in mock_sitecustomize.EXECUTABLE_MODULES_DENY_LIST, "py_compile should be in modules deny list"

    argv = [python_exe] + argv_pattern
    with patch.object(sys, "argv", argv):
        result = mock_sitecustomize.get_first_incompatible_sysarg()
        assert result == "-m py_compile", f"Expected '-m py_compile' for {description} ({python_exe}), got '{result}'"


@pytest.mark.parametrize("python_exe", [PYTHON_INTERPRETERS[4], PYTHON_INTERPRETERS[11], PYTHON_INTERPRETERS[1]])
def test_regular_python_nondenied(mock_sitecustomize, python_exe):
    with patch.object(sys, "argv", [python_exe, "script.py"]):
        result = mock_sitecustomize.get_first_incompatible_sysarg()
        assert result is None, f"Normal python execution should not be denied for {python_exe}, got '{result}'"


@pytest.mark.parametrize(
    "python_exe, module_name, description",
    [
        (PYTHON_INTERPRETERS[4], "json.tool", "python -m json.tool"),
        (PYTHON_INTERPRETERS[11], "json.tool", "python -m json.tool"),
        (PYTHON_INTERPRETERS[8], "json.tool", "python -m json.tool"),
        (PYTHON_INTERPRETERS[4], "pip", "python -m pip"),
        (PYTHON_INTERPRETERS[11], "pip", "python -m pip"),
        (PYTHON_INTERPRETERS[8], "pip", "python -m pip"),
    ],
)
def test_python_module_notdenylist_notdenied(mock_sitecustomize, python_exe, module_name, description):
    argv = [python_exe, "-m", module_name] + (["install", "something"] if module_name == "pip" else [])
    with patch.object(sys, "argv", argv):
        result = mock_sitecustomize.get_first_incompatible_sysarg()
        assert result is None, f"{description} should not be denied for {python_exe}, got '{result}'"


def test_binary_denylist_denied(mock_sitecustomize):
    denied_binaries = ["/usr/bin/py3compile", "/usr/bin/gcc", "/usr/bin/make", "/usr/sbin/chkrootkit"]

    for binary in denied_binaries:
        assert binary in mock_sitecustomize.EXECUTABLES_DENY_LIST, f"{binary} should be in deny list"
        with patch.object(sys, "argv", [binary, "some", "args"]):
            result = mock_sitecustomize.get_first_incompatible_sysarg()
            assert result == binary, f"Expected '{binary}' to be denied, got '{result}'"

    with patch.object(sys, "argv", ["py3compile", "test.py"]):
        result = mock_sitecustomize.get_first_incompatible_sysarg()
        assert result == "py3compile", f"Expected 'py3compile' (basename) to be denied, got '{result}'"


def test_binary_not_in_denylist_allowed(mock_sitecustomize):
    candidate_allowed_binaries = [
        "/usr/bin/python3",
        "/usr/bin/python3.10",
        "/bin/bash",
        "/usr/bin/cat",
        "/usr/bin/ls",
        "/usr/bin/echo",
        "/usr/bin/node",
        "/usr/bin/ruby",
        "/usr/bin/java",
        "/usr/bin/wget",
        "/usr/bin/vim",
        "/usr/bin/nano",
        "/usr/local/bin/custom_app",
    ]

    allowed_binaries = []
    for binary in candidate_allowed_binaries:
        if (
            binary not in mock_sitecustomize.EXECUTABLES_DENY_LIST
            and os.path.basename(binary) not in mock_sitecustomize.EXECUTABLES_DENY_LIST
        ):
            allowed_binaries.append(binary)

    for binary in allowed_binaries:
        with patch.object(sys, "argv", [binary, "some", "args"]):
            result = mock_sitecustomize.get_first_incompatible_sysarg()
            assert result is None, f"Expected '{binary}' to be allowed, but got denied: '{result}'"

    safe_basenames = ["myapp", "custom_script", "user_program"]
    for basename in safe_basenames:
        assert basename not in mock_sitecustomize.EXECUTABLES_DENY_LIST, f"'{basename}' should not be in deny list"

        with patch.object(sys, "argv", [basename, "arg1", "arg2"]):
            result = mock_sitecustomize.get_first_incompatible_sysarg()
            assert result is None, f"Expected '{basename}' to be allowed, but got denied: '{result}'"


@pytest.mark.parametrize("python_exe", PYTHON_INTERPRETERS)
def test_single_argument_not_denied(mock_sitecustomize, python_exe):
    with patch.object(sys, "argv", [python_exe]):
        result = mock_sitecustomize.get_first_incompatible_sysarg()
        assert result is None, f"Single argument should not be denied for {python_exe}, got '{result}'"


@pytest.mark.parametrize("python_exe", [PYTHON_INTERPRETERS[4], PYTHON_INTERPRETERS[11], PYTHON_INTERPRETERS[9]])
def test_m_without_module_not_denied(mock_sitecustomize, python_exe):
    with patch.object(sys, "argv", [python_exe, "-m"]):
        result = mock_sitecustomize.get_first_incompatible_sysarg()
        assert result is None, f"-m without module should not be denied for {python_exe}, got '{result}'"


@pytest.mark.parametrize("python_exe", [PYTHON_INTERPRETERS[1], PYTHON_INTERPRETERS[7], PYTHON_INTERPRETERS[10]])
def test_m_as_last_argument_not_denied(mock_sitecustomize, python_exe):
    with patch.object(sys, "argv", [python_exe, "-v", "-m"]):
        result = mock_sitecustomize.get_first_incompatible_sysarg()
        assert result is None, f"-m as last argument should not be denied for {python_exe}, got '{result}'"


@pytest.mark.parametrize("python_exe", [PYTHON_INTERPRETERS[4], PYTHON_INTERPRETERS[11], PYTHON_INTERPRETERS[8]])
def test_multiple_m_flags_uses_first(mock_sitecustomize, python_exe):
    with patch.object(sys, "argv", [python_exe, "-m", "json.tool", "-m", "py_compile"]):
        result = mock_sitecustomize.get_first_incompatible_sysarg()
        assert result is None, f"First -m should be used (json.tool is allowed) for {python_exe}, got '{result}'"


@pytest.mark.parametrize(
    "python_exe",
    [
        PYTHON_INTERPRETERS[11],
        PYTHON_INTERPRETERS[1],
        PYTHON_INTERPRETERS[2],
        PYTHON_INTERPRETERS[9],
        PYTHON_INTERPRETERS[14],
    ],
)
def test_py_compile_denied_all_interpreters(mock_sitecustomize, python_exe):
    with patch.object(sys, "argv", [python_exe, "-m", "py_compile", "test.py"]):
        result = mock_sitecustomize.get_first_incompatible_sysarg()
        assert result == "-m py_compile", f"py_compile should be denied for {python_exe}, got '{result}'"


def test_missing_sys_argv_not_denied(mock_sitecustomize):
    with patch("builtins.hasattr", return_value=False):
        result = mock_sitecustomize.get_first_incompatible_sysarg()
        assert result is None, f"Missing sys.argv should not be denied, got '{result}'"


def test_non_python_executable_with_m_flag_allowed(mock_sitecustomize):
    assert "py_compile" in mock_sitecustomize.EXECUTABLE_MODULES_DENY_LIST

    non_python_executables = [
        "/bin/whatever",
        "/usr/bin/some_tool",
        "/usr/local/bin/custom_app",
        "/usr/bin/gcc",  # This is actually in deny list, but not for -m
        "/bin/bash",
        "/usr/bin/node",
        "/usr/bin/java",
    ]

    for executable in non_python_executables:
        with patch.object(sys, "argv", [executable, "-m", "py_compile", "test.py"]):
            result = mock_sitecustomize.get_first_incompatible_sysarg()

            if result is not None:
                assert result == executable or result == os.path.basename(
                    executable
                ), f"Expected '{executable}' itself to be denied (if at all), not '-m py_compile'. Got: '{result}'"

        with patch.object(sys, "argv", [executable, "-m", "some_other_module"]):
            result = mock_sitecustomize.get_first_incompatible_sysarg()

            if result is not None:
                assert result == executable or result == os.path.basename(
                    executable
                ), f"Non-Python executable '{executable}' should not be denied for -m patterns. Got: '{result}'"
