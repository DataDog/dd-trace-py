"""Tests for IAST module patching functionality.

This module contains tests for the IAST module patching system, including tests for
IASTFunction and WrapFunctonsForIAST classes.
"""
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from ddtrace.appsec._iast._patch_modules import MODULES_TO_UNPATCH
from ddtrace.appsec._iast._patch_modules import IASTFunction
from ddtrace.appsec._iast._patch_modules import WrapFunctonsForIAST


@pytest.fixture
def mock_module():
    """Create a mock function for testing."""
    return MagicMock()


@pytest.fixture
def mock_hook():
    """Create a mock hook function for testing."""
    return MagicMock()


class TestIASTFunction:
    """Test cases for IASTFunction class."""

    def test_init(self):
        """Test IASTFunction initialization."""
        function = IASTFunction("test_module", "test_function", "test_hook")
        assert function.name == "test_module"
        assert function.function == "test_function"
        assert function.hook == "test_hook"
        assert function.force is False

    def test_init_with_force(self):
        """Test IASTFunction initialization with force=True."""
        function = IASTFunction("test_module", "test_function", "test_hook", force=True)
        assert function.force is True

    @patch("ddtrace.appsec._iast._patch_modules.wrap_object")
    def test_force_wrapper_success(self, mock_wrap):
        """Test force_wrapper when wrapping succeeds."""
        IASTFunction.force_wrapper("test_module", "test_function", "test_hook")
        mock_wrap.assert_called_once()

    @patch("ddtrace.appsec._iast._patch_modules.wrap_object")
    @patch("ddtrace.appsec._iast._patch_modules.iast_instrumentation_wrapt_debug_log")
    def test_force_wrapper_failure(self, mock_log, mock_wrap):
        """Test force_wrapper when wrapping fails."""
        mock_wrap.side_effect = ImportError
        IASTFunction.force_wrapper("test_module", "test_function", "test_hook")
        mock_log.assert_called_once()

    @patch("ddtrace.appsec._iast._patch_modules.try_wrap_function_wrapper")
    def test_patch_lazy(self, mock_wrap):
        """Test patch method with lazy patching."""
        function = IASTFunction("test_module", "test_function", "test_hook")
        result = function.patch()
        assert result is True
        mock_wrap.assert_called_once_with("test_module", "test_function", "test_hook")

    @patch("ddtrace.appsec._iast._patch_modules.wrap_object")
    def test_patch_force(self, mock_wrap):
        """Test patch method with forced patching."""
        function = IASTFunction("test_module", "test_function", "test_hook", force=True)
        result = function.patch()
        assert result is True
        mock_wrap.assert_called_once()

    @patch("ddtrace.appsec._iast._patch_modules.try_unwrap")
    def test_unpatch(self, mock_unwrap):
        """Test unpatch method."""
        function = IASTFunction("test_module", "test_function", "test_hook")
        function.unpatch()
        mock_unwrap.assert_called_once_with("test_module", "test_function")

    def test_repr(self):
        """Test string representation."""
        function = IASTFunction("test_module", "test_function", "test_hook", force=True)
        expected = "IASTFunction(name=test_module, function=test_function, hook=test_hook, force=True)"
        assert repr(function) == expected


class TestWrapModulesForIAST:
    """Test cases for WrapFunctonsForIAST class."""

    @pytest.fixture
    def wrap_modules(self):
        """Create a WrapFunctonsForIAST instance for testing."""
        wrap_modules = WrapFunctonsForIAST()
        yield wrap_modules
        wrap_modules.testing_unpatch()

    def test_init(self, wrap_modules):
        """Test WrapFunctonsForIAST initialization."""
        assert isinstance(wrap_modules.functions, set)
        assert len(wrap_modules.functions) == 0

    def test_add_module(self, wrap_modules):
        """Test adding a function for lazy patching."""
        wrap_modules.wrap_function("test_add_module", "test_function", "test_hook")
        assert len(wrap_modules.functions) == 1
        function = next(iter(wrap_modules.functions))
        assert isinstance(function, IASTFunction)
        assert function.force is False

    def test_add_module_forced(self, wrap_modules):
        """Test adding a function for forced patching."""
        wrap_modules.add_module_forced("test_add_module_forced", "test_function", "test_hook")
        assert len(wrap_modules.functions) == 1
        function = next(iter(wrap_modules.functions))
        assert isinstance(function, IASTFunction)
        assert function.force is True

    @patch("ddtrace.appsec._iast._patch_modules.IASTFunction.patch")
    def test_patch(self, mock_patch, wrap_modules):
        """Test patching all functions."""
        wrap_modules.wrap_function("test_patch", "test_function", "test_hook")
        mock_patch.return_value = True
        wrap_modules.patch()
        mock_patch.assert_called_once()

    @patch("ddtrace.appsec._iast._patch_modules.IASTFunction.patch")
    def test_patch_with_testing(self, mock_patch, wrap_modules):
        """Test patching in testing mode."""
        wrap_modules.testing = True
        wrap_modules.wrap_function("test_patch_with_testing", "test_function", "test_hook")
        mock_patch.return_value = True
        wrap_modules.patch()
        assert len(MODULES_TO_UNPATCH) == 1

    @patch("ddtrace.appsec._iast._patch_modules.IASTFunction.unpatch")
    def test_testing_unpatch(self, mock_unpatch, wrap_modules):
        """Test unpatching in testing mode."""
        wrap_modules.testing = True
        wrap_modules.wrap_function("test_testing_unpatch", "test_function", "test_hook")
        wrap_modules.patch()
        assert len(MODULES_TO_UNPATCH) == 1, f"There are more than 1 function: {MODULES_TO_UNPATCH}"
        wrap_modules.testing_unpatch()
        mock_unpatch.assert_called_once()
        assert len(MODULES_TO_UNPATCH) == 0

    @patch("ddtrace.appsec._iast._patch_modules.IASTFunction.unpatch")
    def test_testing_unpatch_without_testing(self, mock_unpatch, wrap_modules):
        """Test unpatching when not in testing mode."""
        wrap_modules.testing = False
        wrap_modules.wrap_function("test_module", "test_function", "test_hook")
        wrap_modules.patch()
        wrap_modules.testing_unpatch()
        mock_unpatch.assert_not_called()


def test_testing_unpatch_iast():
    """Test the _testing_unpatch_iast utility function."""
    with patch("ddtrace.appsec._iast._patch_modules.WrapFunctonsForIAST") as mock_wrap:
        from ddtrace.appsec._iast._patch_modules import _testing_unpatch_iast

        _testing_unpatch_iast()
        mock_wrap.return_value.testing_unpatch.assert_called_once()
