from ddtrace.internal._instrumentation_enabled import _INSTRUMENTATION_ENABLED


if _INSTRUMENTATION_ENABLED:
    from ddtrace import DDTraceDeprecationWarning
    from ddtrace.contrib.internal.pytest.plugin import is_enabled as is_ddtrace_enabled
    from ddtrace.vendor.debtcollector import deprecate

    def pytest_configure(config):
        if config.pluginmanager.hasplugin("benchmark") and config.pluginmanager.hasplugin("ddtrace"):
            if is_ddtrace_enabled(config):
                deprecate(
                    "this version of the ddtrace.pytest_benchmark plugin is deprecated",
                    message="it will be integrated with the main pytest ddtrace plugin",
                    removal_version="3.0.0",
                    category=DDTraceDeprecationWarning,
                )
