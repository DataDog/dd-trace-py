from ddtrace.internal.utils.importlib import require_modules


required_modules = ["langgraph"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from ddtrace.contrib.internal.langgraph.patch import get_version
        from ddtrace.contrib.internal.langgraph.patch import patch
        from ddtrace.contrib.internal.langgraph.patch import unpatch

        __all__ = ["patch", "unpatch", "get_version"]
