"""Unit tests for ModuleCodeCollector.transform method exclusion logic changes."""

from pathlib import Path
from types import CodeType
from types import ModuleType
from unittest import mock

import pytest

from ddtrace.internal.coverage.code import ModuleCodeCollector
from ddtrace.internal.packages import platlib_path
from ddtrace.internal.packages import stdlib_path


@pytest.fixture(autouse=True)
def cleanup_collector():
    """Ensure ModuleCodeCollector is properly cleaned up after each test."""
    yield
    if ModuleCodeCollector.is_installed():
        ModuleCodeCollector.uninstall()


def test_transform_include_overrides_exclude():
    """Test that include paths override exclude paths for the same file."""
    # Create collector with include paths
    collector = ModuleCodeCollector()
    collector._include_paths = [stdlib_path]  # Include stdlib
    collector._exclude_paths = [stdlib_path]  # But also exclude it

    # Create a mock code object in stdlib
    mock_code = mock.Mock(spec=CodeType)
    mock_code.co_filename = str(stdlib_path / "test_module.py")
    mock_code.co_name = "<module>"

    mock_module = mock.Mock(spec=ModuleType)
    mock_module.__package__ = "test_package"

    with mock.patch("ddtrace.internal.coverage.code.is_user_code", return_value=True):
        with mock.patch.object(collector, "instrument_code", return_value=mock_code) as mock_instrument:
            result = collector.transform(mock_code, mock_module)

            # Should be instrumented because include overrides exclude
            mock_instrument.assert_called_once()
            assert result == mock_code


def test_transform_exclude_without_include():
    """Test that exclusion still works when path is not in include paths."""
    # Create collector with exclude paths but no corresponding include
    collector = ModuleCodeCollector()
    collector._include_paths = [Path("/different/path")]
    collector._exclude_paths = [stdlib_path]

    # Create a mock code object in stdlib
    mock_code = mock.Mock(spec=CodeType)
    mock_code.co_filename = str(stdlib_path / "test_module.py")
    mock_code.co_name = "<module>"

    mock_module = mock.Mock(spec=ModuleType)
    mock_module.__package__ = "test_package"

    with mock.patch("ddtrace.internal.coverage.code.is_user_code", return_value=True):
        with mock.patch.object(collector, "instrument_code") as mock_instrument:
            result = collector.transform(mock_code, mock_module)

            # Should NOT be instrumented because it's excluded and not in include paths
            mock_instrument.assert_not_called()
            assert result == mock_code


def test_transform_multiple_exclude_single_include_override():
    """Test include override with multiple exclude paths but single include match."""
    # Create collector with multiple exclude paths
    collector = ModuleCodeCollector()
    collector._include_paths = [stdlib_path]  # Include stdlib
    collector._exclude_paths = [stdlib_path, platlib_path]  # Exclude both

    # Create a mock code object in stdlib
    mock_code = mock.Mock(spec=CodeType)
    mock_code.co_filename = str(stdlib_path / "test_module.py")
    mock_code.co_name = "<module>"

    mock_module = mock.Mock(spec=ModuleType)
    mock_module.__package__ = "test_package"

    with mock.patch("ddtrace.internal.coverage.code.is_user_code", return_value=True):
        with mock.patch.object(collector, "instrument_code", return_value=mock_code) as mock_instrument:
            result = collector.transform(mock_code, mock_module)

            # Should be instrumented because include overrides exclude
            mock_instrument.assert_called_once()
            assert result == mock_code


def test_transform_multiple_include_multiple_exclude():
    """Test include override with multiple include and exclude paths."""
    # Create collector with multiple paths
    collector = ModuleCodeCollector()
    collector._include_paths = [stdlib_path, platlib_path]  # Include both
    collector._exclude_paths = [stdlib_path, platlib_path]  # But also exclude both

    # Test with stdlib path
    mock_code = mock.Mock(spec=CodeType)
    mock_code.co_filename = str(stdlib_path / "test_module.py")
    mock_code.co_name = "<module>"

    mock_module = mock.Mock(spec=ModuleType)
    mock_module.__package__ = "test_package"

    with mock.patch("ddtrace.internal.coverage.code.is_user_code", return_value=True):
        with mock.patch.object(collector, "instrument_code", return_value=mock_code) as mock_instrument:
            result = collector.transform(mock_code, mock_module)

            # Should be instrumented because include overrides exclude
            mock_instrument.assert_called_once()
            assert result == mock_code


def test_transform_no_include_path_match():
    """Test that code not in include paths is not instrumented, even if not excluded."""
    # Create collector with specific include paths
    collector = ModuleCodeCollector()
    collector._include_paths = [Path("/specific/include/path")]
    collector._exclude_paths = []  # No exclusions

    # Create a mock code object NOT in include paths
    mock_code = mock.Mock(spec=CodeType)
    mock_code.co_filename = "/some/other/path/test_module.py"
    mock_code.co_name = "<module>"

    mock_module = mock.Mock(spec=ModuleType)
    mock_module.__package__ = "test_package"

    with mock.patch("ddtrace.internal.coverage.code.is_user_code", return_value=True):
        with mock.patch.object(collector, "instrument_code") as mock_instrument:
            result = collector.transform(mock_code, mock_module)

            # Should NOT be instrumented because it's not in include paths
            mock_instrument.assert_not_called()
            assert result == mock_code


def test_transform_nested_path_relationships():
    """Test include/exclude logic with nested path relationships."""
    # Create collector with nested paths
    base_path = Path("/base/path")
    nested_path = base_path / "nested"

    collector = ModuleCodeCollector()
    collector._include_paths = [nested_path]  # Include only nested
    collector._exclude_paths = [base_path]  # Exclude base (which includes nested)

    # Test with file in nested path
    mock_code = mock.Mock(spec=CodeType)
    mock_code.co_filename = str(nested_path / "test_module.py")
    mock_code.co_name = "<module>"

    mock_module = mock.Mock(spec=ModuleType)
    mock_module.__package__ = "test_package"

    with mock.patch("ddtrace.internal.coverage.code.is_user_code", return_value=True):
        with mock.patch.object(collector, "instrument_code", return_value=mock_code) as mock_instrument:
            result = collector.transform(mock_code, mock_module)

            # Should be instrumented because include (nested) overrides exclude (base)
            mock_instrument.assert_called_once()
            assert result == mock_code


def test_transform_is_user_code_false():
    """Test that non-user code is not instrumented regardless of include/exclude paths."""
    collector = ModuleCodeCollector()
    collector._include_paths = [Path("/include/path")]
    collector._exclude_paths = []

    # Create a mock code object in include path
    mock_code = mock.Mock(spec=CodeType)
    mock_code.co_filename = "/include/path/test_module.py"
    mock_code.co_name = "<module>"

    mock_module = mock.Mock(spec=ModuleType)
    mock_module.__package__ = "test_package"

    # Mock is_user_code to return False
    with mock.patch("ddtrace.internal.coverage.code.is_user_code", return_value=False):
        with mock.patch.object(collector, "instrument_code") as mock_instrument:
            result = collector.transform(mock_code, mock_module)

            # Should NOT be instrumented because it's not user code
            mock_instrument.assert_not_called()
            assert result == mock_code


def test_transform_none_module():
    """Test that None module returns code unchanged."""
    collector = ModuleCodeCollector()
    collector._include_paths = [Path("/include/path")]

    mock_code = mock.Mock(spec=CodeType)
    mock_code.co_filename = "/include/path/test_module.py"
    mock_code.co_name = "<module>"

    with mock.patch.object(collector, "instrument_code") as mock_instrument:
        result = collector.transform(mock_code, None)

        # Should NOT be instrumented because module is None
        mock_instrument.assert_not_called()
        assert result == mock_code


def test_transform_exclusion_logic_order():
    """Test the exact order of checks in the exclusion logic."""
    collector = ModuleCodeCollector()

    # Set up paths where file is in both include and exclude
    test_path = Path("/test/path")
    collector._include_paths = [test_path]
    collector._exclude_paths = [test_path]

    mock_code = mock.Mock(spec=CodeType)
    mock_code.co_filename = str(test_path / "test_module.py")
    mock_code.co_name = "<module>"

    mock_module = mock.Mock(spec=ModuleType)
    mock_module.__package__ = "test_package"

    # Mock Path.is_relative_to to control the logic flow
    with mock.patch.object(Path, "is_relative_to") as mock_is_relative_to:
        # First call (include check) returns True
        # Second call (exclude check) returns True
        # Third call (include override check) returns True
        mock_is_relative_to.side_effect = [True, True, True]

        with mock.patch("ddtrace.internal.coverage.code.is_user_code", return_value=True):
            with mock.patch.object(collector, "instrument_code", return_value=mock_code) as mock_instrument:
                result = collector.transform(mock_code, mock_module)

                # Should be instrumented because include overrides exclude
                mock_instrument.assert_called_once()
                assert result == mock_code

                # Verify the method was called the expected number of times
                # (once for include check, once for exclude check, once for include override)
                assert mock_is_relative_to.call_count == 3


def test_transform_original_exclusion_behavior():
    """Test that the original exclusion behavior still works when no include override applies."""
    collector = ModuleCodeCollector()

    # Set up paths where file is excluded but not in any include path
    exclude_path = Path("/exclude/path")
    include_path = Path("/different/include/path")

    collector._include_paths = [include_path]
    collector._exclude_paths = [exclude_path]

    mock_code = mock.Mock(spec=CodeType)
    mock_code.co_filename = str(exclude_path / "test_module.py")
    mock_code.co_name = "<module>"

    mock_module = mock.Mock(spec=ModuleType)
    mock_module.__package__ = "test_package"

    with mock.patch("ddtrace.internal.coverage.code.is_user_code", return_value=True):
        with mock.patch.object(collector, "instrument_code") as mock_instrument:
            result = collector.transform(mock_code, mock_module)

            # Should NOT be instrumented because it's excluded and not in include paths
            mock_instrument.assert_not_called()
            assert result == mock_code
