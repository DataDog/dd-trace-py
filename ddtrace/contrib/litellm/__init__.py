# TODO: documentation

from ddtrace.internal.utils.importlib import require_modules


required_modules = ["litellm"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from ddtrace.contrib.internal.litellm.patch import get_version
        from ddtrace.contrib.internal.litellm.patch import patch
        from ddtrace.contrib.internal.litellm.patch import unpatch

        __all__ = ["patch", "unpatch", "get_version"]