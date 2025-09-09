"""Proxy for the main ddtrace pytest plugin."""
from .base import BasePytestPluginProxy

class DdtracePytestPluginProxy(BasePytestPluginProxy):
    def __init__(self):
        super().__init__(
            plugin_module_path="ddtrace.contrib.internal.pytest.plugin",
            plugin_name="ddtrace-pytest"
        )
    
    def _noop_addoption(self, parser):
        """Add main ddtrace options even when disabled to prevent CLI errors."""
        group = parser.getgroup("ddtrace")
        group._addoption(
            "--ddtrace",
            action="store_true",
            dest="ddtrace",
            default=False,
            help="Enable tracing of pytest functions.",
        )
        group._addoption(
            "--no-ddtrace",
            action="store_true",
            dest="no-ddtrace",
            default=False,
            help="Disable tracing of pytest functions.",
        )
        group._addoption(
            "--ddtrace-patch-all",
            action="store_true",
            dest="ddtrace-patch-all",
            default=False,
            help="Call ddtrace._patch_all before running tests.",
        )
        group._addoption(
            "--ddtrace-include-class-name",
            action="store_true",
            dest="ddtrace-include-class-name",
            default=False,
            help="Prepend 'ClassName.' to names of class-based tests.",
        )
        group._addoption(
            "--ddtrace-iast-fail-tests",
            action="store_true",
            dest="ddtrace-iast-fail-tests",
            default=False,
            help="Fail tests that trigger IAST vulnerabilities.",
        )
        
        # Add ini options
        parser.addini("ddtrace", help="Enable tracing of pytest functions.", type="bool")
        parser.addini("no-ddtrace", help="Disable tracing of pytest functions.", type="bool")
        parser.addini("ddtrace-patch-all", help="Call ddtrace._patch_all before running tests.", type="bool")
        parser.addini("ddtrace-include-class-name", help="Prepend 'ClassName.' to names of class-based tests.", type="bool")
    
    def _noop_configure(self, config):
        """Add markers even when disabled."""
        config.addinivalue_line("markers", "dd_tags(**kwargs): add tags to current span")

# Create proxy instance and expose its methods at module level
_proxy = DdtracePytestPluginProxy()

# Dynamically export all pytest hooks
def __getattr__(name):
    return getattr(_proxy, name)

# Explicitly export common hooks for better IDE support
pytest_addoption = _proxy.pytest_addoption
pytest_configure = _proxy.pytest_configure