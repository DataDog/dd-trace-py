from ddtrace.internal.settings._registry import Config
import ddtrace.internal.settings.dynamic_instrumentation  # noqa: F401 — triggers Config.register()


def test_config_get_access_defaults():
    di = Config.get().dynamic_instrumentation
    assert di.enabled is False
    assert di.metrics is True
    assert di.max_payload_size == 1 << 20
    assert di.upload_timeout == 30
    assert di.upload_interval_seconds == 1.0
    assert di.diagnostics_interval == 3600


def test_fresh_root_reads_environment(monkeypatch):
    # Use a fresh Config() (not the cached get()) so the monkeypatched env is read.
    monkeypatch.setenv("DD_DYNAMIC_INSTRUMENTATION_ENABLED", "true")
    monkeypatch.setenv("DD_DYNAMIC_INSTRUMENTATION_METRICS_ENABLED", "false")
    di = Config().dynamic_instrumentation
    assert di.enabled is True
    assert di.metrics is False


def test_get_builds_complete_tree_from_cold_cache():
    # Even with a cold cache, get() eager-imports product modules before building,
    # so the dynamic_instrumentation namespace is always present (the import-order
    # bug the review flagged).
    Config._instance = None
    cfg = Config.get()
    assert hasattr(cfg, "dynamic_instrumentation")
    assert cfg.dynamic_instrumentation.enabled is False
