from ddtrace.internal.utils.importlib import require_modules


required_modules = ["vertexai"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from ..internal.vertexai.patch import get_version
        from ..internal.vertexai.patch import patch
        from ..internal.vertexai.patch import unpatch

        __all__ = ["patch", "unpatch", "get_version"]