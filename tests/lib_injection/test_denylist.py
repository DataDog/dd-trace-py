import os
import sys
from unittest.mock import patch

import pytest


@pytest.fixture
def mock_sitecustomize():
    lib_injection_path = os.path.join(os.path.dirname(__file__), "../../lib-injection/sources")
    if lib_injection_path not in sys.path:
        sys.path.insert(0, lib_injection_path)

    import sitecustomize

    return sitecustomize


def test_python_module_denylist_denied(mock_sitecustomize):
    mock_sitecustomize.EXECUTABLES_DENY_LIST = mock_sitecustomize.build_denied_executables()
    assert "-m py_compile" in mock_sitecustomize.EXECUTABLES_DENY_LIST, "-m py_compile should be in deny list"
    with patch.object(sys, "argv", ["/usr/bin/python3.10", "-m", "py_compile", "test.py"]):
        result = mock_sitecustomize.get_first_incompatible_sysarg()
        assert result == "-m py_compile", f"Expected '-m py_compile', got '{result}'"


def test_regular_python_nondenied(mock_sitecustomize):
    mock_sitecustomize.EXECUTABLES_DENY_LIST = mock_sitecustomize.build_denied_executables()
    with patch.object(sys, "argv", ["/usr/bin/python3.10", "script.py"]):
        result = mock_sitecustomize.get_first_incompatible_sysarg()
        assert result is None, f"Normal python execution should not be denied, got '{result}'"


def test_python_module_notdenylist_notdenied(mock_sitecustomize):
    mock_sitecustomize.EXECUTABLES_DENY_LIST = mock_sitecustomize.build_denied_executables()
    with patch.object(sys, "argv", ["/usr/bin/python3.10", "-m", "json.tool"]):
        result = mock_sitecustomize.get_first_incompatible_sysarg()
        assert result is None, f"python -m json.tool should not be denied, got '{result}'"

    with patch.object(sys, "argv", ["/usr/bin/python3.10", "-m", "pip", "install", "something"]):
        result = mock_sitecustomize.get_first_incompatible_sysarg()
        assert result is None, f"python -m pip should not be denied, got '{result}'"


def test_binary_denylist_denied(mock_sitecustomize):
    mock_sitecustomize.EXECUTABLES_DENY_LIST = mock_sitecustomize.build_denied_executables()

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
    mock_sitecustomize.EXECUTABLES_DENY_LIST = mock_sitecustomize.build_denied_executables()

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


def test_module_denial_edge_cases(mock_sitecustomize):
    mock_sitecustomize.EXECUTABLES_DENY_LIST = mock_sitecustomize.build_denied_executables()

    with patch.object(sys, "argv", ["/usr/bin/python3.10"]):
        result = mock_sitecustomize.get_first_incompatible_sysarg()
        assert result is None, f"Single argument should not be denied, got '{result}'"

    with patch.object(sys, "argv", ["/usr/bin/python3.10", "-m"]):
        result = mock_sitecustomize.get_first_incompatible_sysarg()
        assert result is None, f"-m without module should not be denied, got '{result}'"

    with patch("builtins.hasattr", return_value=False):
        result = mock_sitecustomize.get_first_incompatible_sysarg()
        assert result is None, f"Missing sys.argv should not be denied, got '{result}'"
