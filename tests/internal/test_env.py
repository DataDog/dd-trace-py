import logging
import os
import sys
import warnings

import pytest

# Acquire the env submodule via sys.modules because
# ddtrace.internal.settings.__init__ re-exports dd_environ as `env`, which
# shadows the submodule name for normal `import ... as ...` forms.
from ddtrace.internal.settings.env import dd_environ  # noqa: I100
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning


env_module = sys.modules["ddtrace.internal.settings.env"]
ENV_LOGGER = env_module.__name__


@pytest.fixture(autouse=True)
def reset_warned_keys():
    env_module._warned_keys.clear()
    if hasattr(env_module, "_deprecation_warned"):
        env_module._deprecation_warned.clear()
    yield
    env_module._warned_keys.clear()
    if hasattr(env_module, "_deprecation_warned"):
        env_module._deprecation_warned.clear()


def test_registered_key_no_warning(caplog):
    with caplog.at_level(logging.DEBUG, logger=ENV_LOGGER):
        env_module._validate_key("DD_AGENT_HOST")
    assert caplog.text == ""


def test_unregistered_dd_key_logs_once(caplog):
    with caplog.at_level(logging.DEBUG, logger=ENV_LOGGER):
        env_module._validate_key("DD_TOTALLY_BOGUS_XYZ")
        env_module._validate_key("DD_TOTALLY_BOGUS_XYZ")
    assert caplog.text.count("DD_TOTALLY_BOGUS_XYZ") == 1
    assert "Unsupported Datadog configuration variable accessed" in caplog.text


def test_unregistered_otel_key_logs(caplog):
    with caplog.at_level(logging.DEBUG, logger=ENV_LOGGER):
        env_module._validate_key("OTEL_TOTALLY_BOGUS_XYZ")
    assert "OTEL_TOTALLY_BOGUS_XYZ" in caplog.text


def test_non_datadog_key_skipped(caplog):
    with caplog.at_level(logging.DEBUG, logger=ENV_LOGGER):
        env_module._validate_key("PATH")
        env_module._validate_key("HOME")
    assert caplog.text == ""


def test_unregistered_underscore_dd_key_logs(caplog):
    with caplog.at_level(logging.DEBUG, logger=ENV_LOGGER):
        env_module._validate_key("_DD_TOTALLY_BOGUS_XYZ")
    assert "_DD_TOTALLY_BOGUS_XYZ" in caplog.text


def test_unregistered_datadog_key_logs(caplog):
    with caplog.at_level(logging.DEBUG, logger=ENV_LOGGER):
        env_module._validate_key("DATADOG_TOTALLY_BOGUS_XYZ")
    assert "DATADOG_TOTALLY_BOGUS_XYZ" in caplog.text


def test_alias_is_accepted(caplog):
    # Pick an arbitrary registered alias from the generated registry
    alias = next(iter(env_module._ALIAS_TARGETS))
    with caplog.at_level(logging.DEBUG, logger=ENV_LOGGER):
        env_module._validate_key(alias)
    assert caplog.text == ""


def test_deprecated_key_logs_deprecated_message(caplog):
    if not env_module.DEPRECATED_CONFIGURATIONS:
        pytest.skip("no deprecated configurations registered")
    deprecated_key = next(iter(env_module.DEPRECATED_CONFIGURATIONS))
    with caplog.at_level(logging.DEBUG, logger=ENV_LOGGER):
        env_module._validate_key(deprecated_key)
    assert "Deprecated Datadog configuration variable accessed" in caplog.text
    assert deprecated_key in caplog.text


def test_getitem_reads_from_os_environ(monkeypatch):
    monkeypatch.setenv("DD_AGENT_HOST", "myhost.example.com")
    assert dd_environ["DD_AGENT_HOST"] == "myhost.example.com"


def test_getitem_raises_keyerror_when_missing(monkeypatch):
    monkeypatch.delenv("DD_AGENT_HOST", raising=False)
    with pytest.raises(KeyError):
        dd_environ["DD_AGENT_HOST"]


def test_setitem_writes_to_os_environ(monkeypatch):
    monkeypatch.delenv("DD_AGENT_HOST", raising=False)
    dd_environ["DD_AGENT_HOST"] = "set-via-dd-environ"
    try:
        assert os.environ["DD_AGENT_HOST"] == "set-via-dd-environ"
    finally:
        del os.environ["DD_AGENT_HOST"]


def test_delitem_removes_from_os_environ(monkeypatch):
    monkeypatch.setenv("DD_AGENT_HOST", "x")
    del dd_environ["DD_AGENT_HOST"]
    assert "DD_AGENT_HOST" not in os.environ


def test_contains_true_for_set_key(monkeypatch):
    monkeypatch.setenv("DD_AGENT_HOST", "x")
    assert "DD_AGENT_HOST" in dd_environ


def test_contains_false_for_unset_key(monkeypatch):
    monkeypatch.delenv("DD_AGENT_HOST", raising=False)
    assert "DD_AGENT_HOST" not in dd_environ


def test_contains_warns_on_unregistered_key(caplog, monkeypatch):
    monkeypatch.delenv("DD_UNREGISTERED_CONTAINS_TEST", raising=False)
    with caplog.at_level(logging.DEBUG, logger=ENV_LOGGER):
        result = "DD_UNREGISTERED_CONTAINS_TEST" in dd_environ
    assert result is False
    assert "DD_UNREGISTERED_CONTAINS_TEST" in caplog.text


def test_len_and_iter_match_os_environ():
    assert len(dd_environ) == len(os.environ)
    assert set(dd_environ) == set(os.environ)


def test_copy_returns_plain_dict():
    snapshot = dd_environ.copy()
    assert isinstance(snapshot, dict)
    assert snapshot == dict(os.environ)


def _pick_alias_pair() -> tuple[str, str]:
    """Return (canonical, legacy) for the first registered rename alias."""
    canonical, aliases = next(iter(env_module.CONFIGURATION_ALIASES.items()))
    return canonical, aliases[0]


def test_getitem_falls_back_to_alias_when_canonical_missing(monkeypatch):
    canonical, legacy = _pick_alias_pair()
    monkeypatch.delenv(canonical, raising=False)
    monkeypatch.setenv(legacy, "from-legacy")
    assert dd_environ[canonical] == "from-legacy"


def test_getitem_prefers_canonical_over_alias(monkeypatch):
    canonical, legacy = _pick_alias_pair()
    monkeypatch.setenv(canonical, "from-canonical")
    monkeypatch.setenv(legacy, "from-legacy")
    assert dd_environ[canonical] == "from-canonical"


def test_getitem_raises_when_neither_canonical_nor_alias_set(monkeypatch):
    canonical, legacy = _pick_alias_pair()
    monkeypatch.delenv(canonical, raising=False)
    monkeypatch.delenv(legacy, raising=False)
    with pytest.raises(KeyError):
        dd_environ[canonical]


def test_legacy_direct_access_does_not_fall_back_to_canonical(monkeypatch):
    canonical, legacy = _pick_alias_pair()
    monkeypatch.setenv(canonical, "from-canonical")
    monkeypatch.delenv(legacy, raising=False)
    with pytest.raises(KeyError):
        dd_environ[legacy]


def test_contains_true_when_only_alias_is_set(monkeypatch):
    canonical, legacy = _pick_alias_pair()
    monkeypatch.delenv(canonical, raising=False)
    monkeypatch.setenv(legacy, "x")
    assert canonical in dd_environ


def test_contains_false_when_neither_canonical_nor_alias_set(monkeypatch):
    canonical, legacy = _pick_alias_pair()
    monkeypatch.delenv(canonical, raising=False)
    monkeypatch.delenv(legacy, raising=False)
    assert canonical not in dd_environ


def test_get_config_falls_back_to_alias_in_local_config(monkeypatch):
    from ddtrace.internal import telemetry as telemetry_mod

    canonical, legacy = _pick_alias_pair()
    monkeypatch.delenv(canonical, raising=False)
    monkeypatch.delenv(legacy, raising=False)
    monkeypatch.setattr(telemetry_mod, "LOCAL_CONFIG", {legacy: "from-local"})
    monkeypatch.setattr(telemetry_mod, "FLEET_CONFIG", {})

    val = telemetry_mod.get_config(canonical, default=None, report_telemetry=False)
    assert val == "from-local"


def test_get_config_falls_back_to_alias_in_fleet_config(monkeypatch):
    from ddtrace.internal import telemetry as telemetry_mod

    canonical, legacy = _pick_alias_pair()
    monkeypatch.delenv(canonical, raising=False)
    monkeypatch.delenv(legacy, raising=False)
    monkeypatch.setattr(telemetry_mod, "LOCAL_CONFIG", {})
    monkeypatch.setattr(telemetry_mod, "FLEET_CONFIG", {legacy: "from-fleet"})
    monkeypatch.setattr(telemetry_mod, "FLEET_CONFIG_IDS", {})

    val = telemetry_mod.get_config(canonical, default=None, report_telemetry=False)
    assert val == "from-fleet"


def test_reading_deprecated_set_var_emits_ddtrace_deprecation_warning(monkeypatch):
    monkeypatch.setenv("DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED", "false")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DDTraceDeprecationWarning)
        env_module.dd_environ.get("DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED")
    messages = [str(w.message) for w in caught if issubclass(w.category, DDTraceDeprecationWarning)]
    assert any("DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED" in m for m in messages)
    assert any("5.0.0" in m for m in messages)


def test_reading_deprecated_alias_emits_ddtrace_deprecation_warning(monkeypatch):
    monkeypatch.setenv("DD_TRACE_INFERRED_SPANS_ENABLED", "true")
    monkeypatch.delenv("DD_TRACE_INFERRED_PROXY_SERVICES_ENABLED", raising=False)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DDTraceDeprecationWarning)
        env_module.dd_environ.get("DD_TRACE_INFERRED_PROXY_SERVICES_ENABLED")
    messages = [str(w.message) for w in caught if issubclass(w.category, DDTraceDeprecationWarning)]
    assert any("DD_TRACE_INFERRED_SPANS_ENABLED" in m for m in messages)


def test_deprecation_warning_fires_only_once_per_key(monkeypatch):
    monkeypatch.setenv("DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED", "false")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DDTraceDeprecationWarning)
        env_module.dd_environ.get("DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED")
        env_module.dd_environ.get("DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED")
        env_module.dd_environ["DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED"]
    messages = [
        str(w.message) for w in caught
        if issubclass(w.category, DDTraceDeprecationWarning) and "DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED" in str(w.message)
    ]
    assert len(messages) == 1


def test_contains_check_does_not_fire_deprecation_warning(monkeypatch):
    # __contains__ probes existence — should not fire a user-facing deprecation warning.
    monkeypatch.delenv("DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED", raising=False)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DDTraceDeprecationWarning)
        _ = "DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED" in env_module.dd_environ
    relevant = [w for w in caught if "DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED" in str(w.message)]
    assert relevant == []


def test_deprecated_unset_var_does_not_fire_warning(monkeypatch):
    # A deprecated var that the user did not set should not fire a warning on read.
    monkeypatch.delenv("DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED", raising=False)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DDTraceDeprecationWarning)
        env_module.dd_environ.get("DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED", "true")
    relevant = [w for w in caught if "DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED" in str(w.message)]
    assert relevant == []
