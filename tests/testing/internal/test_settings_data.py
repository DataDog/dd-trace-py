from __future__ import annotations

import pytest

from ddtrace.testing.internal.settings_data import TestManagementSettings


SAMPLE_ATTRIBUTES = {
    "enabled": True,
    "attempt_to_fix_retries": 30,
}


class TestTestManagementSettingsFromAttributes:
    def test_retries_from_api(self) -> None:
        """When the env var is not set, the API value is used."""
        settings = TestManagementSettings.from_attributes(SAMPLE_ATTRIBUTES)
        assert settings.attempt_to_fix_retries == 30

    def test_retries_from_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When the env var is set to a valid integer, it overrides the API value."""
        monkeypatch.setenv("DD_TEST_MANAGEMENT_ATTEMPT_TO_FIX_RETRIES", "5")
        settings = TestManagementSettings.from_attributes(SAMPLE_ATTRIBUTES)
        assert settings.attempt_to_fix_retries == 5

    def test_retries_env_var_overrides_api(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The env var takes precedence over a non-default API value."""
        monkeypatch.setenv("DD_TEST_MANAGEMENT_ATTEMPT_TO_FIX_RETRIES", "10")
        attributes = {**SAMPLE_ATTRIBUTES, "attempt_to_fix_retries": 99}
        settings = TestManagementSettings.from_attributes(attributes)
        assert settings.attempt_to_fix_retries == 10

    def test_retries_env_var_invalid_non_digit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When the env var is not a valid integer, the API value is used."""
        monkeypatch.setenv("DD_TEST_MANAGEMENT_ATTEMPT_TO_FIX_RETRIES", "not-a-number")
        settings = TestManagementSettings.from_attributes(SAMPLE_ATTRIBUTES)
        assert settings.attempt_to_fix_retries == 30

    def test_retries_env_var_empty_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When the env var is an empty string, the API value is used."""
        monkeypatch.setenv("DD_TEST_MANAGEMENT_ATTEMPT_TO_FIX_RETRIES", "")
        settings = TestManagementSettings.from_attributes(SAMPLE_ATTRIBUTES)
        assert settings.attempt_to_fix_retries == 30

    def test_enabled_from_attributes(self) -> None:
        """The enabled flag is always taken from the API attributes."""
        settings = TestManagementSettings.from_attributes(SAMPLE_ATTRIBUTES)
        assert settings.enabled is True

        disabled_settings = TestManagementSettings.from_attributes({**SAMPLE_ATTRIBUTES, "enabled": False})
        assert disabled_settings.enabled is False
