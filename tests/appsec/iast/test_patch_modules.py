"""Tests for IAST module patching functionality.

This module contains tests for the IAST module patching system, including tests for
IASTModule and WrapModulesForIAST classes.
"""
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from ddtrace.appsec._iast._patch_modules import MODULES_TO_UNPATCH
from ddtrace.appsec._iast._patch_modules import IASTModule
from ddtrace.appsec._iast._patch_modules import WrapModulesForIAST


@pytest.fixture
def mock_module():
    """Create a mock module for testing."""
    return MagicMock()


@pytest.fixture
def mock_hook():
    """Create a mock hook function for testing."""
    return MagicMock()


class TestIASTModule:
    """Test cases for IASTModule class."""

    def test_init(self):
        """Test IASTModule initialization."""
        module = IASTModule("test_module", "test_function", "test_hook")
        assert module.name == "test_module"
        assert module.function == "test_function"
        assert module.hook == "test_hook"
        assert module.force is False

    def test_init_with_force(self):
        """Test IASTModule initialization with force=True."""
        module = IASTModule("test_module", "test_function", "test_hook", force=True)
        assert module.force is True

    @patch("ddtrace.appsec._iast._patch_modules.wrap_object")
    def test_force_wrapper_success(self, mock_wrap):
        """Test force_wrapper when wrapping succeeds."""
        IASTModule.force_wrapper("test_module", "test_function", "test_hook")
        mock_wrap.assert_called_once()

    @patch("ddtrace.appsec._iast._patch_modules.wrap_object")
    @patch("ddtrace.appsec._iast._patch_modules.iast_instrumentation_wrapt_debug_log")
    def test_force_wrapper_failure(self, mock_log, mock_wrap):
        """Test force_wrapper when wrapping fails."""
        mock_wrap.side_effect = ImportError
        IASTModule.force_wrapper("test_module", "test_function", "test_hook")
        mock_log.assert_called_once()

    @patch("ddtrace.appsec._iast._patch_modules.try_wrap_function_wrapper")
    def test_patch_lazy(self, mock_wrap):
        """Test patch method with lazy patching."""
        module = IASTModule("test_module", "test_function", "test_hook")
        result = module.patch()
        assert result is True
        mock_wrap.assert_called_once_with("test_module", "test_function", "test_hook")

    @patch("ddtrace.appsec._iast._patch_modules.wrap_object")
    def test_patch_force(self, mock_wrap):
        """Test patch method with forced patching."""
        module = IASTModule("test_module", "test_function", "test_hook", force=True)
        result = module.patch()
        assert result is True
        mock_wrap.assert_called_once()

    @patch("ddtrace.appsec._iast._patch_modules.try_unwrap")
    def test_unpatch(self, mock_unwrap):
        """Test unpatch method."""
        module = IASTModule("test_module", "test_function", "test_hook")
        module.unpatch()
        mock_unwrap.assert_called_once_with("test_module", "test_function")

    def test_repr(self):
        """Test string representation."""
        module = IASTModule("test_module", "test_function", "test_hook", force=True)
        expected = "IASTModule(name=test_module, function=test_function, hook=test_hook, force=True)"
        assert repr(module) == expected


class TestWrapModulesForIAST:
    """Test cases for WrapModulesForIAST class."""

    @pytest.fixture
    def wrap_modules(self):
        """Create a WrapModulesForIAST instance for testing."""
        return WrapModulesForIAST()

    def test_init(self, wrap_modules):
        """Test WrapModulesForIAST initialization."""
        assert isinstance(wrap_modules.modules, set)
        assert len(wrap_modules.modules) == 0

    def test_add_module(self, wrap_modules):
        """Test adding a module for lazy patching."""
        wrap_modules.add_module("test_module", "test_function", "test_hook")
        assert len(wrap_modules.modules) == 1
        module = next(iter(wrap_modules.modules))
        assert isinstance(module, IASTModule)
        assert module.force is False

    def test_add_module_forced(self, wrap_modules):
        """Test adding a module for forced patching."""
        wrap_modules.add_module_forced("test_module", "test_function", "test_hook")
        assert len(wrap_modules.modules) == 1
        module = next(iter(wrap_modules.modules))
        assert isinstance(module, IASTModule)
        assert module.force is True

    @patch("ddtrace.appsec._iast._patch_modules.IASTModule.patch")
    def test_patch(self, mock_patch, wrap_modules):
        """Test patching all modules."""
        wrap_modules.add_module("test_module", "test_function", "test_hook")
        mock_patch.return_value = True
        wrap_modules.patch()
        mock_patch.assert_called_once()

    @patch("ddtrace.appsec._iast._patch_modules.IASTModule.patch")
    def test_patch_with_testing(self, mock_patch, wrap_modules):
        """Test patching in testing mode."""
        wrap_modules.testing = True
        wrap_modules.add_module("test_module", "test_function", "test_hook")
        mock_patch.return_value = True
        wrap_modules.patch()
        assert len(MODULES_TO_UNPATCH) == 1

    @patch("ddtrace.appsec._iast._patch_modules.IASTModule.unpatch")
    def test_testing_unpatch(self, mock_unpatch, wrap_modules):
        """Test unpatching in testing mode."""
        wrap_modules.testing = True
        wrap_modules.add_module("test_module", "test_function", "test_hook")
        wrap_modules.patch()
        assert len(MODULES_TO_UNPATCH) == 1
        wrap_modules.testing_unpatch()
        mock_unpatch.assert_called_once()
        assert len(MODULES_TO_UNPATCH) == 0

    @patch("ddtrace.appsec._iast._patch_modules.IASTModule.unpatch")
    def test_testing_unpatch_without_testing(self, mock_unpatch, wrap_modules):
        """Test unpatching when not in testing mode."""
        wrap_modules.testing = False
        wrap_modules.add_module("test_module", "test_function", "test_hook")
        wrap_modules.patch()
        wrap_modules.testing_unpatch()
        mock_unpatch.assert_not_called()


def test_testing_unpatch_iast():
    """Test the _testing_unpatch_iast utility function."""
    with patch("ddtrace.appsec._iast._patch_modules.WrapModulesForIAST") as mock_wrap:
        from ddtrace.appsec._iast._patch_modules import _testing_unpatch_iast

        _testing_unpatch_iast()
        mock_wrap.return_value.testing_unpatch.assert_called_once()
