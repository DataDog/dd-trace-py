"""Proxy for the ddtrace pytest-benchmark plugin."""
from .base import BasePytestPluginProxy

class DdtracePytestBenchmarkPluginProxy(BasePytestPluginProxy):
    def __init__(self):
        super().__init__(
            plugin_module_path="ddtrace.contrib.internal.pytest_benchmark.plugin", 
            plugin_name="ddtrace-pytest-benchmark"
        )

# Create proxy instance and expose its methods at module level  
_proxy = DdtracePytestBenchmarkPluginProxy()

# Export all hooks dynamically
def __getattr__(name):
    return getattr(_proxy, name)