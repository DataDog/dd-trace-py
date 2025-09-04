def setup_tracing():
    import ddtrace.auto  # noqa:F401
    from ray.util.tracing import tracing_helper
    from ray.util.tracing.tracing_helper import _is_tracing_enabled

    tracing_helper._global_is_tracing_enabled = False
    assert not _is_tracing_enabled(), "OTEL Tracing should be disabled at setup."
