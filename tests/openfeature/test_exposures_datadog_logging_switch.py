import pytest

from ddtrace.internal.settings.openfeature import OpenFeatureConfig


NEW = "DD_FEATURE_FLAGS_EXPOSURES_DATADOG_LOGGING_ENABLED"
OLD = "DD_FFE_INTAKE_ENABLED"


@pytest.mark.parametrize(
    "env, expected",
    [
        ({}, True),
        ({NEW: "false"}, False),
        ({OLD: "false"}, False),
        ({NEW: "true", OLD: "false"}, True),
        ({NEW: "false", OLD: "true"}, False),
    ],
)
def test_exposures_datadog_logging_enabled(monkeypatch, env, expected):
    monkeypatch.delenv(NEW, raising=False)
    monkeypatch.delenv(OLD, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    assert OpenFeatureConfig().exposures_datadog_logging_enabled is expected


@pytest.mark.subprocess(env={"DD_FFE_INTAKE_ENABLED": "false"})
def test_deprecated_intake_switch_warns():
    import warnings

    from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        import ddtrace.internal.settings.openfeature as settings

    messages = [str(w.message) for w in caught if issubclass(w.category, DDTraceDeprecationWarning)]
    assert any(
        "DD_FFE_INTAKE_ENABLED is deprecated" in m and "DD_FEATURE_FLAGS_EXPOSURES_DATADOG_LOGGING_ENABLED" in m
        for m in messages
    ), messages
    assert settings.config.exposures_datadog_logging_enabled is False


@pytest.mark.subprocess(env={"DD_FEATURE_FLAGS_EXPOSURES_DATADOG_LOGGING_ENABLED": "false"})
def test_new_switch_does_not_warn():
    import warnings

    from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        import ddtrace.internal.settings.openfeature as settings

    assert not [w for w in caught if issubclass(w.category, DDTraceDeprecationWarning)]
    assert settings.config.exposures_datadog_logging_enabled is False
