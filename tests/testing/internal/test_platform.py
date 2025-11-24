"""Tests for ddtestpy.internal.platform module."""

import platform

from ddtestpy.internal.platform import PlatformTag
from ddtestpy.internal.platform import get_platform_tags


class TestPlatformTag:
    """Tests for PlatformTag constants."""

    def test_platform_tag_constants(self) -> None:
        """Test that PlatformTag constants are defined correctly."""
        assert PlatformTag.OS_ARCHITECTURE == "os.architecture"
        assert PlatformTag.OS_PLATFORM == "os.platform"
        assert PlatformTag.OS_VERSION == "os.version"
        assert PlatformTag.RUNTIME_NAME == "runtime.name"
        assert PlatformTag.RUNTIME_VERSION == "runtime.version"


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
