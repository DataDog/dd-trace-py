"""Test coverage for environment variable include paths and exclusion logic changes."""
import pytest


@pytest.mark.subprocess
def test_coverage_env_include_paths_single_path():
    """Test that a single path from _DD_CIVISIBILITY_COVERAGE_INCLUDE_PATHS
    environment variable is correctly processed.
    """
    import os
    from pathlib import Path
    import tempfile
    from unittest import mock

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        env_path = temp_path / "env_included"
        env_path.mkdir()

        # Mock the environment variable
        with mock.patch.dict(os.environ, {"_DD_CIVISIBILITY_COVERAGE_INCLUDE_PATHS": str(env_path)}):
            install(include_paths=[temp_path])

            # Verify the environment path was added to include paths
            assert ModuleCodeCollector._instance is not None
            include_paths = ModuleCodeCollector._instance._include_paths

            # Should contain both the original path and the env path
            assert temp_path in include_paths
            assert env_path in include_paths
            assert len(include_paths) == 2


@pytest.mark.subprocess
def test_coverage_env_include_paths_multiple_paths():
    """Test that multiple paths from _DD_CIVISIBILITY_COVERAGE_INCLUDE_PATHS
    environment variable are correctly processed.
    """
    import os
    from pathlib import Path
    import tempfile
    from unittest import mock

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        env_path1 = temp_path / "env_included1"
        env_path2 = temp_path / "env_included2"
        env_path1.mkdir()
        env_path2.mkdir()

        # Mock the environment variable with multiple paths
        env_paths = f"{env_path1}{os.pathsep}{env_path2}"
        with mock.patch.dict(os.environ, {"_DD_CIVISIBILITY_COVERAGE_INCLUDE_PATHS": env_paths}):
            install(include_paths=[temp_path])

            # Verify both environment paths were added to include paths
            assert ModuleCodeCollector._instance is not None
            include_paths = ModuleCodeCollector._instance._include_paths

            # Should contain the original path and both env paths
            assert temp_path in include_paths
            assert env_path1 in include_paths
            assert env_path2 in include_paths
            assert len(include_paths) == 3


@pytest.mark.subprocess
def test_coverage_env_include_paths_with_whitespace():
    """Test that paths with leading/trailing whitespace are correctly stripped."""
    import os
    from pathlib import Path
    import tempfile
    from unittest import mock

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        env_path = temp_path / "env_included"
        env_path.mkdir()

        # Mock the environment variable with whitespace
        env_paths = f"  {env_path}  {os.pathsep}  "
        with mock.patch.dict(os.environ, {"_DD_CIVISIBILITY_COVERAGE_INCLUDE_PATHS": env_paths}):
            install(include_paths=[temp_path])

            # Verify the environment path was added with whitespace stripped
            assert ModuleCodeCollector._instance is not None
            include_paths = ModuleCodeCollector._instance._include_paths

            # Should contain both paths, whitespace should be stripped
            assert temp_path in include_paths
            assert env_path in include_paths
            assert len(include_paths) == 2


@pytest.mark.subprocess
def test_coverage_env_include_paths_empty_entries():
    """Test that empty path entries are filtered out."""
    import os
    from pathlib import Path
    import tempfile
    from unittest import mock

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        env_path = temp_path / "env_included"
        env_path.mkdir()

        # Mock the environment variable with empty entries
        env_paths = f"{env_path}{os.pathsep}{os.pathsep}{os.pathsep}"
        with mock.patch.dict(os.environ, {"_DD_CIVISIBILITY_COVERAGE_INCLUDE_PATHS": env_paths}):
            install(include_paths=[temp_path])

            # Verify only non-empty paths were added
            assert ModuleCodeCollector._instance is not None
            include_paths = ModuleCodeCollector._instance._include_paths

            # Should contain only the original path and the valid env path
            assert temp_path in include_paths
            assert env_path in include_paths
            assert len(include_paths) == 2


@pytest.mark.subprocess
def test_coverage_env_include_paths_no_env_var():
    """Test that behavior is unchanged when environment variable is not set."""
    import os
    from pathlib import Path
    import tempfile
    from unittest import mock

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Ensure the environment variable is not set
        with mock.patch.dict(os.environ, {}, clear=False):
            if "_DD_CIVISIBILITY_COVERAGE_INCLUDE_PATHS" in os.environ:
                del os.environ["_DD_CIVISIBILITY_COVERAGE_INCLUDE_PATHS"]

            install(include_paths=[temp_path])

            # Verify only the original path is included
            assert ModuleCodeCollector._instance is not None
            include_paths = ModuleCodeCollector._instance._include_paths

            assert temp_path in include_paths
            assert len(include_paths) == 1


@pytest.mark.subprocess
def test_coverage_env_include_paths_empty_env_var():
    """Test that empty environment variable doesn't affect behavior."""
    import os
    from pathlib import Path
    import tempfile
    from unittest import mock

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Mock the environment variable as empty
        with mock.patch.dict(os.environ, {"_DD_CIVISIBILITY_COVERAGE_INCLUDE_PATHS": ""}):
            install(include_paths=[temp_path])

            # Verify only the original path is included
            assert ModuleCodeCollector._instance is not None
            include_paths = ModuleCodeCollector._instance._include_paths

            assert temp_path in include_paths
            assert len(include_paths) == 1


@pytest.mark.subprocess
def test_coverage_include_override_exclude():
    """Test that include paths override exclude paths in the transform method."""
    from pathlib import Path
    import tempfile
    from types import CodeType
    from types import ModuleType
    from unittest import mock

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from ddtrace.internal.packages import stdlib_path

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Create a mock code object that would normally be excluded (in stdlib_path)
        # but should be included because it's also in our include paths
        stdlib_subpath = stdlib_path / "test_module.py"

        install(include_paths=[temp_path])
        collector = ModuleCodeCollector._instance

        # Mock a code object that appears to be in stdlib
        mock_code = mock.Mock(spec=CodeType)
        mock_code.co_filename = str(stdlib_subpath)
        mock_code.co_name = "<module>"

        mock_module = mock.Mock(spec=ModuleType)
        mock_module.__package__ = "test_package"

        # Add stdlib_path to our include paths to simulate the override
        collector._include_paths.append(stdlib_path)

        # Mock is_user_code to return True for our test
        with mock.patch("ddtrace.internal.coverage.code.is_user_code", return_value=True):
            with mock.patch.object(collector, "instrument_code", return_value=mock_code) as mock_instrument:
                result = collector.transform(mock_code, mock_module)

                # The code should be instrumented because include path overrides exclude path
                mock_instrument.assert_called_once()
                assert result == mock_code


@pytest.mark.subprocess
def test_coverage_exclude_without_include_override():
    """Test that exclusion still works when path is not in include paths."""
    from pathlib import Path
    import tempfile
    from types import CodeType
    from types import ModuleType
    from unittest import mock

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from ddtrace.internal.packages import stdlib_path

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Create a mock code object that would be excluded (in stdlib_path)
        # and is NOT in our include paths
        stdlib_subpath = stdlib_path / "test_module.py"

        install(include_paths=[temp_path])
        collector = ModuleCodeCollector._instance

        # Mock a code object that appears to be in stdlib
        mock_code = mock.Mock(spec=CodeType)
        mock_code.co_filename = str(stdlib_subpath)
        mock_code.co_name = "<module>"

        mock_module = mock.Mock(spec=ModuleType)
        mock_module.__package__ = "test_package"

        # Mock is_user_code to return True for our test
        with mock.patch("ddtrace.internal.coverage.code.is_user_code", return_value=True):
            with mock.patch.object(collector, "instrument_code") as mock_instrument:
                result = collector.transform(mock_code, mock_module)

                # The code should NOT be instrumented because it's excluded and not in include paths
                mock_instrument.assert_not_called()
                assert result == mock_code


@pytest.mark.subprocess
def test_coverage_include_paths_used_with_env_var():
    """Integration test verifying environment variable paths are used during actual coverage collection."""
    import os
    from pathlib import Path
    import tempfile
    from unittest import mock

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        env_path = temp_path / "env_coverage"
        env_path.mkdir()

        # Mock the environment variable
        with mock.patch.dict(os.environ, {"_DD_CIVISIBILITY_COVERAGE_INCLUDE_PATHS": str(env_path)}):
            install(include_paths=[temp_path])

            # Verify the collector was set up with the env path
            assert ModuleCodeCollector._instance is not None
            include_paths = ModuleCodeCollector._instance._include_paths

            assert temp_path in include_paths
            assert env_path in include_paths
            assert len(include_paths) == 2
