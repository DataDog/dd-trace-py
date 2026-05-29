import pytest

from ddtrace.internal.settings._core import DDConfig
from ddtrace.internal.settings._core import field


class _DemoDI(DDConfig):
    __prefix__ = "dd.dynamic_instrumentation"

    enabled = field()  # -> DD_DYNAMIC_INSTRUMENTATION_ENABLED, bool, default False
    metrics = field("metrics.enabled")  # -> DD_DYNAMIC_INSTRUMENTATION_METRICS_ENABLED, bool, default True
    max_payload_size = field()  # -> ...MAX_PAYLOAD_SIZE, int, default 1048576
    upload_interval_seconds = field("upload.interval_seconds")  # decimal -> float, 1.0


def test_field_resolves_type_and_default_from_registry():
    cfg = _DemoDI()
    assert cfg.enabled is False
    assert cfg.metrics is True
    assert cfg.max_payload_size == 1 << 20
    assert cfg.upload_interval_seconds == 1.0


def test_field_reads_environment(monkeypatch):
    monkeypatch.setenv("DD_DYNAMIC_INSTRUMENTATION_ENABLED", "true")
    monkeypatch.setenv("DD_DYNAMIC_INSTRUMENTATION_MAX_PAYLOAD_SIZE", "4096")
    cfg = _DemoDI()
    assert cfg.enabled is True
    assert cfg.max_payload_size == 4096


def test_field_rename_reads_correct_env_var(monkeypatch):
    # attribute `metrics` but env var is ..._METRICS_ENABLED
    monkeypatch.setenv("DD_DYNAMIC_INSTRUMENTATION_METRICS_ENABLED", "false")
    assert _DemoDI().metrics is False


def test_plain_config_without_field_is_unaffected():
    class _Plain(DDConfig):
        __prefix__ = "dd.demo"
        x = DDConfig.v(int, "x", default=3)

    assert _Plain().x == 3


def test_field_not_in_registry_raises():
    with pytest.raises(ValueError):

        class _Bad(DDConfig):
            __prefix__ = "dd.dynamic_instrumentation"
            nope = field("totally_made_up_xyz_123")
