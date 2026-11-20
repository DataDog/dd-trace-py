"""Tests for ddtrace.testing.internal.platform module."""

import json
import logging
import platform

import pytest

from ddtrace.testing.internal.platform import PlatformTag
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


class TestGetPlatformTags:
    """Tests for get_platform_tags function."""

    @pytest.fixture(autouse=True)
    def _no_runtime_tags_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DD_TEST_OPTIMIZATION_RUNTIME_TAGS", raising=False)

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


class TestRuntimeTagsOverrides:
    """Tests for DD_TEST_OPTIMIZATION_RUNTIME_TAGS."""

    def test_overrides_supported_tags_and_keeps_others(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "DD_TEST_OPTIMIZATION_RUNTIME_TAGS",
            json.dumps({"os.version": " 6.12.0 ", "runtime.version": "3.11.0", "runtime.vendor": "ignored"}),
        )
        result = get_platform_tags()
        assert result[PlatformTag.OS_VERSION] == "6.12.0"
        assert result[PlatformTag.RUNTIME_VERSION] == "3.11.0"
        assert result[PlatformTag.OS_PLATFORM] == platform.system()
        assert "runtime.vendor" not in result

    def test_overrides_every_supported_tag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        overrides = {
            "os.architecture": "x86_64",
            "os.platform": "Linux",
            "os.version": "6.12.0",
            "runtime.name": "CPython",
            "runtime.version": "3.11.0",
        }
        monkeypatch.setenv("DD_TEST_OPTIMIZATION_RUNTIME_TAGS", json.dumps(overrides))
        assert get_platform_tags() == overrides

    @pytest.mark.parametrize("value", [None, "", " ", "\t", 11, ["6.12.0"], "\ud800"])
    def test_ignores_unusable_values(self, monkeypatch: pytest.MonkeyPatch, value: object) -> None:
        monkeypatch.setenv("DD_TEST_OPTIMIZATION_RUNTIME_TAGS", json.dumps({"os.version": value}))
        assert get_platform_tags()[PlatformTag.OS_VERSION] == platform.release()

    @pytest.mark.parametrize("value", ["", "   "])
    def test_empty_variable_sets_no_override(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, value: str
    ) -> None:
        monkeypatch.setenv("DD_TEST_OPTIMIZATION_RUNTIME_TAGS", value)
        assert get_platform_tags()[PlatformTag.OS_VERSION] == platform.release()
        assert "DD_TEST_OPTIMIZATION_RUNTIME_TAGS" not in caplog.text

    @pytest.mark.parametrize("value", ["{invalid json}", '["os.version"]', '"6.12.0"', "null"])
    def test_invalid_value_logs_warning_and_keeps_detected_tags(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, value: str
    ) -> None:
        monkeypatch.setenv("DD_TEST_OPTIMIZATION_RUNTIME_TAGS", value)
        with caplog.at_level(logging.WARNING, logger="ddtrace.testing.internal.platform"):
            result = get_platform_tags()
        assert result[PlatformTag.OS_VERSION] == platform.release()
        assert "DD_TEST_OPTIMIZATION_RUNTIME_TAGS" in caplog.text

    def test_deeply_nested_value_logs_warning_and_keeps_detected_tags(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DD_TEST_OPTIMIZATION_RUNTIME_TAGS", '{"custom.tag":' + "[" * 100_000)
        assert get_platform_tags()[PlatformTag.OS_VERSION] == platform.release()
