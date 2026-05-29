from pathlib import Path

import pytest


def _fresh_config():
    # Re-instantiate so monkeypatched env vars are picked up via dd_environ.
    from ddtrace.internal.settings.dynamic_instrumentation import DynamicInstrumentationConfig

    return DynamicInstrumentationConfig()


def test_defaults_match_legacy():
    cfg = _fresh_config()
    assert cfg.enabled is False
    assert cfg.metrics is True
    assert cfg.max_payload_size == 1 << 20
    assert cfg.upload_timeout == 30
    assert cfg.upload_interval_seconds == 1.0
    assert cfg.diagnostics_interval == 3600
    assert cfg.probe_file is None
    assert cfg.redacted_identifiers == set()
    assert cfg.redacted_types == set()
    assert cfg.redaction_excluded_identifiers == set()
    assert cfg.redacted_types_re is None
    assert cfg.global_rate_limit == 100.0
    assert cfg._tags_in_qs is True
    assert isinstance(cfg.service_name, str) and cfg.service_name
    assert isinstance(cfg.tags, str)


def test_generated_scalars_read_from_environment(monkeypatch):
    monkeypatch.setenv("DD_DYNAMIC_INSTRUMENTATION_ENABLED", "true")
    monkeypatch.setenv("DD_DYNAMIC_INSTRUMENTATION_MAX_PAYLOAD_SIZE", "2048")
    monkeypatch.setenv("DD_DYNAMIC_INSTRUMENTATION_UPLOAD_TIMEOUT", "5")
    monkeypatch.setenv("DD_DYNAMIC_INSTRUMENTATION_UPLOAD_INTERVAL_SECONDS", "2.5")
    monkeypatch.setenv("DD_DYNAMIC_INSTRUMENTATION_DIAGNOSTICS_INTERVAL", "60")
    cfg = _fresh_config()
    assert cfg.enabled is True
    assert cfg.max_payload_size == 2048
    assert cfg.upload_timeout == 5
    assert cfg.upload_interval_seconds == 2.5
    assert cfg.diagnostics_interval == 60


def test_metrics_attribute_reads_metrics_enabled_env(monkeypatch):
    # The attribute is `metrics` but the env var is ..._METRICS_ENABLED.
    monkeypatch.setenv("DD_DYNAMIC_INSTRUMENTATION_METRICS_ENABLED", "false")
    cfg = _fresh_config()
    assert cfg.metrics is False


def test_hand_owned_map_and_validator(monkeypatch):
    monkeypatch.setenv("DD_DYNAMIC_INSTRUMENTATION_REDACTED_IDENTIFIERS", "Foo_Bar, baz")
    monkeypatch.setenv("DD_DYNAMIC_INSTRUMENTATION_REDACTED_TYPES", "a.b, c")
    cfg = _fresh_config()
    assert cfg.redacted_identifiers == {"foobar", "baz"}
    assert cfg.redacted_types == {"a.b", "c"}
    assert cfg.redacted_types_re is not None
    assert cfg.redacted_types_re.match("a.b")


def test_redacted_types_validator_rejects_bad_pattern(monkeypatch):
    monkeypatch.setenv("DD_DYNAMIC_INSTRUMENTATION_REDACTED_TYPES", "1bad")
    with pytest.raises(ValueError):
        _fresh_config()


def test_probe_file_is_a_path(monkeypatch):
    monkeypatch.setenv("DD_DYNAMIC_INSTRUMENTATION_PROBE_FILE", "/tmp/probes.json")
    cfg = _fresh_config()
    assert cfg.probe_file == Path("/tmp/probes.json")


def test_public_attribute_surface_is_complete():
    cfg = _fresh_config()
    for attr in (
        "enabled",
        "metrics",
        "max_payload_size",
        "upload_timeout",
        "upload_interval_seconds",
        "diagnostics_interval",
        "probe_file",
        "redacted_identifiers",
        "redacted_types",
        "redaction_excluded_identifiers",
        "redacted_types_re",
        "service_name",
        "_intake_url",
        "global_rate_limit",
        "_tags_in_qs",
        "tags",
    ):
        assert hasattr(cfg, attr), attr


def test_injected_var_value_source_and_parsed():
    from ddtrace.internal.settings._core import ValueSource

    cfg = _fresh_config()
    assert cfg.parsed.enabled is False
    assert cfg.value_source("DD_DYNAMIC_INSTRUMENTATION_ENABLED") == ValueSource.DEFAULT
