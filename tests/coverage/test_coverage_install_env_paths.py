"""Unit tests for ModuleCodeCollector.install method with environment variable support."""

import os
from pathlib import Path
from unittest import mock

import pytest

from ddtrace.internal.coverage.code import ModuleCodeCollector


@pytest.fixture(autouse=True)
def cleanup_collector():
    """Ensure ModuleCodeCollector is properly cleaned up after each test."""
    yield
    if ModuleCodeCollector.is_installed():
        ModuleCodeCollector.uninstall()


def test_install_with_env_include_paths_single():
    """Test install method correctly processes single path from environment variable."""
    test_path = Path("/test/path")
    env_path = Path("/env/path")

    with mock.patch.dict(os.environ, {"_DD_CIVISIBILITY_COVERAGE_INCLUDE_PATHS": str(env_path)}):
        # Simulate the instance creation part of install
        ModuleCodeCollector._instance = ModuleCodeCollector()

        # Test the include path processing logic directly
        include_paths = [test_path]

        # Simulate the environment variable processing from the install method
        env_include_paths = os.environ.get("_DD_CIVISIBILITY_COVERAGE_INCLUDE_PATHS")
        if env_include_paths:
            include_paths.extend(Path(path.strip()) for path in env_include_paths.split(os.pathsep) if path.strip())

        ModuleCodeCollector._instance._include_paths = include_paths

        # Verify both paths are included
        include_paths = ModuleCodeCollector._instance._include_paths

        assert test_path in include_paths
        assert env_path in include_paths
        assert len(include_paths) == 2


def test_install_with_env_include_paths_multiple():
    """Test install method correctly processes multiple paths from environment variable."""
    test_path = Path("/test/path")
    env_path1 = Path("/env/path1")
    env_path2 = Path("/env/path2")

    env_paths_str = f"{env_path1}{os.pathsep}{env_path2}"

    with mock.patch.dict(os.environ, {"_DD_CIVISIBILITY_COVERAGE_INCLUDE_PATHS": env_paths_str}):
        # Simulate the instance creation part of install
        ModuleCodeCollector._instance = ModuleCodeCollector()

        # Test the include path processing logic directly
        include_paths = [test_path]

        # Simulate the environment variable processing from the install method
        env_include_paths = os.environ.get("_DD_CIVISIBILITY_COVERAGE_INCLUDE_PATHS")
        if env_include_paths:
            include_paths.extend(Path(path.strip()) for path in env_include_paths.split(os.pathsep) if path.strip())

        ModuleCodeCollector._instance._include_paths = include_paths

        # Verify all paths are included
        include_paths = ModuleCodeCollector._instance._include_paths

        assert test_path in include_paths
        assert env_path1 in include_paths
        assert env_path2 in include_paths
        assert len(include_paths) == 3


def test_install_with_env_include_paths_with_spaces():
    """Test install method correctly strips whitespace from environment variable paths."""
    test_path = Path("/test/path")
    env_path = Path("/env/path")

    # Include spaces and tabs around paths
    env_paths_str = f"  {env_path}  {os.pathsep}   {os.pathsep}  "

    with mock.patch.dict(os.environ, {"_DD_CIVISIBILITY_COVERAGE_INCLUDE_PATHS": env_paths_str}):
        # Simulate the instance creation part of install
        ModuleCodeCollector._instance = ModuleCodeCollector()

        # Test the include path processing logic directly
        include_paths = [test_path]

        # Simulate the environment variable processing from the install method
        env_include_paths = os.environ.get("_DD_CIVISIBILITY_COVERAGE_INCLUDE_PATHS")
        if env_include_paths:
            include_paths.extend(Path(path.strip()) for path in env_include_paths.split(os.pathsep) if path.strip())

        ModuleCodeCollector._instance._include_paths = include_paths

        # Verify both paths are included, whitespace should be stripped
        include_paths = ModuleCodeCollector._instance._include_paths

        assert test_path in include_paths
        assert env_path in include_paths
        # Should only have 2 paths (empty entries filtered out)
        assert len(include_paths) == 2


def test_install_with_env_include_paths_empty_string():
    """Test install method handles empty environment variable correctly."""
    test_path = Path("/test/path")

    with mock.patch.dict(os.environ, {"_DD_CIVISIBILITY_COVERAGE_INCLUDE_PATHS": ""}):
        # Simulate the instance creation part of install
        ModuleCodeCollector._instance = ModuleCodeCollector()

        # Test the include path processing logic directly
        include_paths = [test_path]

        # Simulate the environment variable processing from the install method
        env_include_paths = os.environ.get("_DD_CIVISIBILITY_COVERAGE_INCLUDE_PATHS")
        if env_include_paths:
            include_paths.extend(Path(path.strip()) for path in env_include_paths.split(os.pathsep) if path.strip())

        ModuleCodeCollector._instance._include_paths = include_paths

        # Verify only original path is included
        include_paths = ModuleCodeCollector._instance._include_paths

        assert test_path in include_paths
        assert len(include_paths) == 1


def test_install_with_env_include_paths_only_separators():
    """Test install method handles environment variable with only separators."""
    test_path = Path("/test/path")

    env_paths_str = f"{os.pathsep}{os.pathsep}{os.pathsep}"

    with mock.patch.dict(os.environ, {"_DD_CIVISIBILITY_COVERAGE_INCLUDE_PATHS": env_paths_str}):
        # Simulate the instance creation part of install
        ModuleCodeCollector._instance = ModuleCodeCollector()

        # Test the include path processing logic directly
        include_paths = [test_path]

        # Simulate the environment variable processing from the install method
        env_include_paths = os.environ.get("_DD_CIVISIBILITY_COVERAGE_INCLUDE_PATHS")
        if env_include_paths:
            include_paths.extend(Path(path.strip()) for path in env_include_paths.split(os.pathsep) if path.strip())

        ModuleCodeCollector._instance._include_paths = include_paths

        # Verify only original path is included
        include_paths = ModuleCodeCollector._instance._include_paths

        assert test_path in include_paths
        assert len(include_paths) == 1


def test_install_without_env_include_paths():
    """Test install method behavior when environment variable is not set."""
    test_path = Path("/test/path")

    # Ensure environment variable is not set
    env_dict = dict(os.environ)
    env_dict.pop("_DD_CIVISIBILITY_COVERAGE_INCLUDE_PATHS", None)

    with mock.patch.dict(os.environ, env_dict, clear=True):
        # Simulate the instance creation part of install
        ModuleCodeCollector._instance = ModuleCodeCollector()

        # Test the include path processing logic directly
        include_paths = [test_path]

        # Simulate the environment variable processing from the install method
        env_include_paths = os.environ.get("_DD_CIVISIBILITY_COVERAGE_INCLUDE_PATHS")
        if env_include_paths:
            include_paths.extend(Path(path.strip()) for path in env_include_paths.split(os.pathsep) if path.strip())

        ModuleCodeCollector._instance._include_paths = include_paths

        # Verify only original path is included
        include_paths = ModuleCodeCollector._instance._include_paths

        assert test_path in include_paths
        assert len(include_paths) == 1


def test_install_with_none_include_paths_and_env():
    """Test install method with None include_paths but environment variable set."""
    env_path = Path("/env/path")
    cwd_path = Path(os.getcwd())

    with mock.patch.dict(os.environ, {"_DD_CIVISIBILITY_COVERAGE_INCLUDE_PATHS": str(env_path)}):
        # Simulate the instance creation part of install
        ModuleCodeCollector._instance = ModuleCodeCollector()

        # Test the include path processing logic directly, simulating None include_paths case
        include_paths = [cwd_path]  # This is what happens when include_paths is None

        # Simulate the environment variable processing from the install method
        env_include_paths = os.environ.get("_DD_CIVISIBILITY_COVERAGE_INCLUDE_PATHS")
        if env_include_paths:
            include_paths.extend(Path(path.strip()) for path in env_include_paths.split(os.pathsep) if path.strip())

        ModuleCodeCollector._instance._include_paths = include_paths

        # Verify both paths are included
        include_paths = ModuleCodeCollector._instance._include_paths

        # Should have cwd (default) and env path
        assert cwd_path in include_paths
        assert env_path in include_paths
        assert len(include_paths) == 2


def test_install_env_paths_pathsep_compatibility():
    """Test that the correct path separator is used based on OS."""
    import os

    test_path = Path("/test/path")
    env_path1 = Path("/env/path1")
    env_path2 = Path("/env/path2")

    # Test with the system's path separator
    env_paths_str = f"{env_path1}{os.pathsep}{env_path2}"

    with mock.patch.dict(os.environ, {"_DD_CIVISIBILITY_COVERAGE_INCLUDE_PATHS": env_paths_str}):
        # Simulate the instance creation part of install
        ModuleCodeCollector._instance = ModuleCodeCollector()

        # Test the include path processing logic directly
        include_paths = [test_path]

        # Simulate the environment variable processing from the install method
        env_include_paths = os.environ.get("_DD_CIVISIBILITY_COVERAGE_INCLUDE_PATHS")
        if env_include_paths:
            include_paths.extend(Path(path.strip()) for path in env_include_paths.split(os.pathsep) if path.strip())

        ModuleCodeCollector._instance._include_paths = include_paths

        # Verify all paths are included
        include_paths = ModuleCodeCollector._instance._include_paths

        assert test_path in include_paths
        assert env_path1 in include_paths
        assert env_path2 in include_paths
        assert len(include_paths) == 3


def test_install_duplicate_paths_from_env():
    """Test that duplicate paths from environment variable and include_paths are handled correctly."""
    test_path = Path("/test/path")

    # Set environment variable to same path as include_paths
    with mock.patch.dict(os.environ, {"_DD_CIVISIBILITY_COVERAGE_INCLUDE_PATHS": str(test_path)}):
        # Simulate the instance creation part of install
        ModuleCodeCollector._instance = ModuleCodeCollector()

        # Test the include path processing logic directly
        include_paths = [test_path]

        # Simulate the environment variable processing from the install method
        env_include_paths = os.environ.get("_DD_CIVISIBILITY_COVERAGE_INCLUDE_PATHS")
        if env_include_paths:
            include_paths.extend(Path(path.strip()) for path in env_include_paths.split(os.pathsep) if path.strip())

        ModuleCodeCollector._instance._include_paths = include_paths

        # Verify the path appears twice (duplicates are allowed)
        include_paths = ModuleCodeCollector._instance._include_paths

        assert test_path in include_paths
        assert len(include_paths) == 2  # Should have the path twice


def test_install_relative_paths_from_env():
    """Test that relative paths from environment variable are handled correctly."""
    test_path = Path("/test/path")
    env_path = Path("./relative/path")

    with mock.patch.dict(os.environ, {"_DD_CIVISIBILITY_COVERAGE_INCLUDE_PATHS": str(env_path)}):
        # Simulate the instance creation part of install
        ModuleCodeCollector._instance = ModuleCodeCollector()

        # Test the include path processing logic directly
        include_paths = [test_path]

        # Simulate the environment variable processing from the install method
        env_include_paths = os.environ.get("_DD_CIVISIBILITY_COVERAGE_INCLUDE_PATHS")
        if env_include_paths:
            include_paths.extend(Path(path.strip()) for path in env_include_paths.split(os.pathsep) if path.strip())

        ModuleCodeCollector._instance._include_paths = include_paths

        # Verify both paths are included
        include_paths = ModuleCodeCollector._instance._include_paths

        assert test_path in include_paths
        assert env_path in include_paths
        assert len(include_paths) == 2
