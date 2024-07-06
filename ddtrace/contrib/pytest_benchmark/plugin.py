from ddtrace.contrib.pytest.plugin import is_enabled as is_ddtrace_enabled


def pytest_configure(config):
    if config.pluginmanager.hasplugin("benchmark") and config.pluginmanager.hasplugin("ddtrace"):
        if is_ddtrace_enabled(config):
            from ._plugin import _PytestBenchmarkPlugin

            config.pluginmanager.register(_PytestBenchmarkPlugin(), "_datadog-pytest-benchmark")
