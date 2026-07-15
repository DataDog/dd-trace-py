"""Maintain a separate module for the version to avoid circular imports."""

import importlib.metadata


__all__ = ["__version__"]

__version__: str

try:
    distributions = importlib.metadata.packages_distributions().get(__package__ or __name__)
except Exception:
    distributions = None

try:
    # ``version()`` can return None (not raise) when stale coexisting
    # ddtrace-*.dist-info directories are present, which would leave
    # ``__version__`` as None and crash consumers that expect a string.
    # Coerce any falsy result to the same fallback the except-branch uses.
    __version__ = importlib.metadata.version(distributions[0] if distributions else "ddtrace") or "0.0.0"
except Exception:
    __version__ = "0.0.0"
