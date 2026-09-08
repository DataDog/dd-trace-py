"""Maintain a separate module for the version to avoid circular imports."""

import importlib.metadata


__all__ = ["__version__", *sorted(_LAZY_EXPORTS)]

__version__: str

try:
    __version__ = importlib.metadata.version("ddtrace")
except Exception:
    try:
       _distributions = importlib.metadata.packages_distributions().get(__package__ or __name__)
        __version__ = importlib.metadata.version(_distributions[0] if _distributions else "ddtrace")
    except Exception:
        __version__ = "0.0.0"