"""
Unit tests for the -m module denial functionality in sitecustomize.py.
These tests directly test the denial logic without requiring the complex venv fixtures.
"""
import os
import sys
import tempfile
from unittest.mock import patch

import pytest


@pytest.fixture
def mock_sitecustomize():
    """Mock the sitecustomize module for testing."""
    # Add the lib-injection sources to sys.path
    lib_injection_path = os.path.join(os.path.dirname(__file__), "../../lib-injection/sources")
    if lib_injection_path not in sys.path:
        sys.path.insert(0, lib_injection_path)

    # Import the module
    import sitecustomize

    return sitecustomize


def test_python_module_denylist_denied(mock_sitecustomize):
    """Test that -m py_compile is detected and denied."""
    # Build the deny list
    mock_sitecustomize.EXECUTABLES_DENY_LIST = mock_sitecustomize.build_denied_executables()

    # Verify -m py_compile is in the deny list
    assert "-m py_compile" in mock_sitecustomize.EXECUTABLES_DENY_LIST, "-m py_compile should be in deny list"

    # Test detection of -m py_compile pattern
    with patch.object(sys, "argv", ["/usr/bin/python3.10", "-m", "py_compile", "test.py"]):
        result = mock_sitecustomize.get_first_incompatible_sysarg()
        assert result == "-m py_compile", f"Expected '-m py_compile', got '{result}'"


def test_regular_python_nondenied(mock_sitecustomize):
    """Test that normal Python execution is not denied."""
    mock_sitecustomize.EXECUTABLES_DENY_LIST = mock_sitecustomize.build_denied_executables()

    # Test normal python execution
    with patch.object(sys, "argv", ["/usr/bin/python3.10", "script.py"]):
        result = mock_sitecustomize.get_first_incompatible_sysarg()
        assert result is None, f"Normal python execution should not be denied, got '{result}'"


def test_python_module_notdenylist_notdenied(mock_sitecustomize):
    """Test that other -m modules are not denied."""
    mock_sitecustomize.EXECUTABLES_DENY_LIST = mock_sitecustomize.build_denied_executables()

    # Test -m json.tool (should not be denied)
    with patch.object(sys, "argv", ["/usr/bin/python3.10", "-m", "json.tool"]):
        result = mock_sitecustomize.get_first_incompatible_sysarg()
        assert result is None, f"python -m json.tool should not be denied, got '{result}'"

    # Test -m pip (should not be denied)
    with patch.object(sys, "argv", ["/usr/bin/python3.10", "-m", "pip", "install", "something"]):
        result = mock_sitecustomize.get_first_incompatible_sysarg()
        assert result is None, f"python -m pip should not be denied, got '{result}'"


def test_binary_denylist_denied(mock_sitecustomize):
    """Test that /usr/bin/py3compile is still denied (existing functionality)."""
    mock_sitecustomize.EXECUTABLES_DENY_LIST = mock_sitecustomize.build_denied_executables()

    # Verify /usr/bin/py3compile is in deny list
    assert (
        "/usr/bin/py3compile" in mock_sitecustomize.EXECUTABLES_DENY_LIST
    ), "/usr/bin/py3compile should be in deny list"

    # Test traditional py3compile execution
    with patch.object(sys, "argv", ["/usr/bin/py3compile", "test.py"]):
        result = mock_sitecustomize.get_first_incompatible_sysarg()
        assert result == "/usr/bin/py3compile", f"Expected '/usr/bin/py3compile', got '{result}'"


def test_module_denial_edge_cases(mock_sitecustomize):
    """Test edge cases for the module denial logic."""
    mock_sitecustomize.EXECUTABLES_DENY_LIST = mock_sitecustomize.build_denied_executables()

    # Test insufficient arguments (should not crash)
    with patch.object(sys, "argv", ["/usr/bin/python3.10"]):
        result = mock_sitecustomize.get_first_incompatible_sysarg()
        assert result is None, f"Single argument should not be denied, got '{result}'"

    # Test -m without module name (should not crash)
    with patch.object(sys, "argv", ["/usr/bin/python3.10", "-m"]):
        result = mock_sitecustomize.get_first_incompatible_sysarg()
        assert result is None, f"-m without module should not be denied, got '{result}'"

    # Test no sys.argv (should not crash)
    with patch("builtins.hasattr", return_value=False):
        result = mock_sitecustomize.get_first_incompatible_sysarg()
        assert result is None, f"Missing sys.argv should not be denied, got '{result}'"
