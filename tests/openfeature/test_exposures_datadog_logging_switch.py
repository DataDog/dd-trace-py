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


@pytest.mark.parametrize(
    "fleet, env, local, expected, source",
    [
        ({OLD: "false"}, {}, {}, False, "fleet_stable_config"),
        ({}, {}, {OLD: "false"}, False, "local_stable_config"),
        ({OLD: "true"}, {OLD: "false"}, {}, True, "fleet_stable_config"),
        ({}, {OLD: "true"}, {OLD: "false"}, True, "env_var"),
        ({OLD: "false"}, {}, {NEW: "true"}, True, "local_stable_config"),
        ({}, {OLD: "false"}, {NEW: "true"}, True, "local_stable_config"),
        ({NEW: "false"}, {OLD: "true"}, {}, False, "fleet_stable_config"),
        ({}, {}, {}, True, "default"),
    ],
)
def test_deprecated_switch_in_every_source(monkeypatch, fleet, env, local, expected, source):
    from ddtrace.internal.settings import _core

    monkeypatch.delenv(NEW, raising=False)
    monkeypatch.delenv(OLD, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(_core, "FLEET_CONFIG", fleet)
    monkeypatch.setattr(_core, "LOCAL_CONFIG", local)

    config = OpenFeatureConfig()

    assert config.exposures_datadog_logging_enabled is expected
    assert config.value_source(NEW) == source


@pytest.mark.parametrize("name", [NEW, OLD])
def test_fleet_config_id_is_reported_for_either_name(monkeypatch, name):
    from ddtrace.internal.settings import _core

    monkeypatch.delenv(NEW, raising=False)
    monkeypatch.delenv(OLD, raising=False)
    monkeypatch.setattr(_core, "FLEET_CONFIG", {name: "false"})
    monkeypatch.setattr(_core, "FLEET_CONFIG_IDS", {name: "fleet-123"})
    monkeypatch.setattr(_core, "LOCAL_CONFIG", {})

    config = OpenFeatureConfig()

    assert config.exposures_datadog_logging_enabled is False
    assert config.config_id(NEW) == "fleet-123"


@pytest.mark.subprocess(parametrize={"_DD_SC_FILE_ENV": ["_DD_SC_LOCAL_FILE_OVERRIDE", "_DD_SC_MANAGED_FILE_OVERRIDE"]})
def test_deprecated_switch_in_stable_config_file_warns():
    import os
    import tempfile
    import warnings

    with tempfile.NamedTemporaryFile(suffix=".yaml") as stable_config:
        stable_config.write(b'apm_configuration_default:\n  DD_FFE_INTAKE_ENABLED: "false"\n')
        stable_config.flush()
        # Set before importing ddtrace, which reads stable configuration once.
        os.environ[os.environ["_DD_SC_FILE_ENV"]] = stable_config.name

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            import ddtrace.internal.settings.openfeature as settings
            from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning

    assert settings.config.exposures_datadog_logging_enabled is False
    assert any(
        "DD_FFE_INTAKE_ENABLED is deprecated" in str(w.message)
        for w in caught
        if issubclass(w.category, DDTraceDeprecationWarning)
    )
