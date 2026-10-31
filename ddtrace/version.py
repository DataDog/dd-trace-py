"""Maintain a separate module for the version to avoid circular imports."""

import importlib.metadata


__all__ = ["__version__"]

__version__: str

# ``version()`` can return None (not raise) when stale coexisting
# ddtrace-*.dist-info directories are present, which would leave
# ``__version__`` as None and crash consumers that expect a string.
# Coerce a falsy result to the same fallback the except-branches use.
try:
    __version__ = importlib.metadata.version("ddtrace") or "0.0.0"
except Exception:
    try:
        _distributions = importlib.metadata.packages_distributions().get(__package__ or __name__)
        __version__ = importlib.metadata.version(_distributions[0] if _distributions else "ddtrace") or "0.0.0"
    except Exception:
        __version__ = "0.0.0"
