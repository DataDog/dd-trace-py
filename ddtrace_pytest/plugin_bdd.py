"""Proxy for the ddtrace pytest-bdd plugin."""
from .base import BasePytestPluginProxy

class DdtracePytestBddPluginProxy(BasePytestPluginProxy):
    def __init__(self):
        super().__init__(
            plugin_module_path="ddtrace.contrib.internal.pytest_bdd.plugin",
            plugin_name="ddtrace-pytest-bdd"
        )

# Create proxy instance and expose its methods at module level
_proxy = DdtracePytestBddPluginProxy()

# Export all hooks dynamically
def __getattr__(name):
    return getattr(_proxy, name)