import pytest

from ddtrace.internal.settings._core import ValueSource
from ddtrace.internal.settings.openfeature import OpenFeatureConfig


@pytest.mark.parametrize(
    "canonical,legacy,expected",
    [
        (None, None, True),
        ("true", None, True),
        ("false", None, False),
        (None, "true", True),
        (None, "false", False),
        ("true", "false", True),
        ("false", "true", False),
        ("", "true", False),
        ("invalid", "true", False),
        (None, "", False),
        (None, "invalid", False),
    ],
)
def test_evaluation_count_environment_alias(monkeypatch, canonical, legacy, expected):
    for name, value in (
        ("DD_FEATURE_FLAGS_EVALUATION_COUNTS_ENABLED", canonical),
        ("DD_FLAGGING_EVALUATION_COUNTS_ENABLED", legacy),
    ):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    config = OpenFeatureConfig()

    assert config.flagging_evaluation_counts_enabled is expected
    expected_source = ValueSource.DEFAULT if canonical is None and legacy is None else ValueSource.ENV_VAR
    assert config.value_source("DD_FEATURE_FLAGS_EVALUATION_COUNTS_ENABLED") == expected_source


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize(
    "env_name", ["DD_FEATURE_FLAGS_EVALUATION_COUNTS_ENABLED", "DD_FLAGGING_EVALUATION_COUNTS_ENABLED"]
)
def test_evaluation_counts_preserves_code_override(monkeypatch, env_name, enabled):
    monkeypatch.delenv("DD_FEATURE_FLAGS_EVALUATION_COUNTS_ENABLED", raising=False)
    monkeypatch.delenv("DD_FLAGGING_EVALUATION_COUNTS_ENABLED", raising=False)
    monkeypatch.setenv(env_name, str(not enabled).lower())
    config = OpenFeatureConfig()

    config.flagging_evaluation_counts_enabled = enabled

    assert config.flagging_evaluation_counts_enabled is enabled
