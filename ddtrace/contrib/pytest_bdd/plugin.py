from ddtrace import DDTraceDeprecationWarning
from ddtrace.contrib.pytest._utils import _USE_PLUGIN_V2
from ddtrace.contrib.pytest.plugin import is_enabled as is_ddtrace_enabled
from ddtrace.vendor.debtcollector import deprecate


def pytest_configure(config):
    if config.pluginmanager.hasplugin("pytest-bdd") and config.pluginmanager.hasplugin("ddtrace"):
        if not _USE_PLUGIN_V2:
            if is_ddtrace_enabled(config):
                from ._plugin import _PytestBddPlugin

                deprecate(
                    "the ddtrace.pytest_bdd plugin is deprecated",
                    message="it will be integrated with the main pytest ddtrace plugin",
                    removal_version="3.0.0",
                    category=DDTraceDeprecationWarning,
                )

                config.pluginmanager.register(_PytestBddPlugin(), "_datadog-pytest-bdd")
