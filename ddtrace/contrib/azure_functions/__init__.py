from ddtrace.internal.utils.importlib import require_modules


required_modules = ["azure.functions"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        # Required to allow users to import from `ddtrace.contrib.azure_functions.patch` directly
        import warnings as _w

        with _w.catch_warnings():
            _w.simplefilter("ignore", DeprecationWarning)
            from . import patch as _  # noqa: F401, I001

        # Expose public methods
        from ddtrace.contrib.internal.azure_functions.patch import get_version
        from ddtrace.contrib.internal.azure_functions.patch import patch
        from ddtrace.contrib.internal.azure_functions.patch import unpatch

        __all__ = ["patch", "unpatch", "get_version"]
