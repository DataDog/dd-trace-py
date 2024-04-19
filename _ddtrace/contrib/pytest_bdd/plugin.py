from _ddtrace.contrib.pytest.plugin import is_enabled as is_ddtrace_enabled


def pytest_configure(config):
    if config.pluginmanager.hasplugin("pytest-bdd") and config.pluginmanager.hasplugin("ddtrace"):
        if is_ddtrace_enabled(config):
            from ddtrace.contrib.pytest_bdd._plugin import _PytestBddPlugin

            config.pluginmanager.register(_PytestBddPlugin(), "_datadog-pytest-bdd")
