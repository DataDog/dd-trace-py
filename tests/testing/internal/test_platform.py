"""Tests for ddtrace.testing.internal.platform module."""

import platform

import pytest

from ddtrace.testing.internal.platform import PlatformTag
from ddtrace.testing.internal.platform import detect_test_environment_id
from ddtrace.testing.internal.platform import get_platform_tags


class TestPlatformTag:
    """Tests for PlatformTag constants."""

    def test_platform_tag_constants(self) -> None:
        """Test that PlatformTag constants are defined correctly."""
        assert PlatformTag.OS_ARCHITECTURE == "os.architecture"
        assert PlatformTag.OS_PLATFORM == "os.platform"
        assert PlatformTag.OS_VERSION == "os.version"
        assert PlatformTag.RUNTIME_NAME == "runtime.name"
        assert PlatformTag.RUNTIME_VERSION == "runtime.version"
        assert PlatformTag.TEST_ENVIRONMENT_ID == "test.environment.id"


class TestGetPlatformTags:
    """Tests for get_platform_tags function."""

    def test_get_platform_tags_has_all_keys(self) -> None:
        """Test that get_platform_tags returns all expected keys."""
        result = get_platform_tags()
        expected_keys = {
            PlatformTag.OS_ARCHITECTURE,
            PlatformTag.OS_PLATFORM,
            PlatformTag.OS_VERSION,
            PlatformTag.RUNTIME_NAME,
            PlatformTag.RUNTIME_VERSION,
        }
        assert set(result.keys()) == expected_keys

    def test_get_platform_tags_os_architecture(self) -> None:
        """Test that OS architecture is correctly retrieved."""
        result = get_platform_tags()
        assert result[PlatformTag.OS_ARCHITECTURE] == platform.machine()

    def test_get_platform_tags_os_platform(self) -> None:
        """Test that OS platform is correctly retrieved."""
        result = get_platform_tags()
        assert result[PlatformTag.OS_PLATFORM] == platform.system()

    def test_get_platform_tags_os_version(self) -> None:
        """Test that OS version is correctly retrieved."""
        result = get_platform_tags()
        assert result[PlatformTag.OS_VERSION] == platform.release()

    def test_get_platform_tags_runtime_name(self) -> None:
        """Test that runtime name is correctly retrieved."""
        result = get_platform_tags()
        assert result[PlatformTag.RUNTIME_NAME] == platform.python_implementation()

    def test_get_platform_tags_runtime_version(self) -> None:
        """Test that runtime version is correctly retrieved."""
        result = get_platform_tags()
        assert result[PlatformTag.RUNTIME_VERSION] == platform.python_version()

    def test_get_platform_tags_no_empty_values(self) -> None:
        """Test that no values in platform tags are empty."""
        result = get_platform_tags()
        for key, value in result.items():
            assert value, f"Value for {key} should not be empty"


class TestDetectTestEnvironmentId:
    """Tests for detect_test_environment_id function."""

    def test_explicit_env_var_takes_priority(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The _DD_CIVISIBILITY_ITR_TEST_ENVIRONMENT_ID env var wins over all other detection."""
        monkeypatch.setenv("_DD_CIVISIBILITY_ITR_TEST_ENVIRONMENT_ID", "explicit-id")
        monkeypatch.setenv("VIRTUAL_ENV", "/some/path/venv-hash")
        assert detect_test_environment_id() == "explicit-id"

    def test_virtual_env_basename(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When VIRTUAL_ENV is set, its basename is used as the environment id."""
        monkeypatch.delenv("_DD_CIVISIBILITY_ITR_TEST_ENVIRONMENT_ID", raising=False)
        monkeypatch.setenv("VIRTUAL_ENV", "/home/user/.cache/test-environments/a3f2b1c")
        assert detect_test_environment_id() == "a3f2b1c"

    def test_returns_none_when_no_venv(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns None when no virtual environment is detected and no env var is set."""
        monkeypatch.delenv("_DD_CIVISIBILITY_ITR_TEST_ENVIRONMENT_ID", raising=False)
        monkeypatch.delenv("VIRTUAL_ENV", raising=False)
        # This test only passes when running outside a venv (sys.prefix == sys.base_prefix).
        # In CI, tests typically run inside a venv, so we skip in that case.
        import sys

        if sys.prefix != sys.base_prefix:
            pytest.skip("Test requires running outside a virtual environment")
        assert detect_test_environment_id() is None
