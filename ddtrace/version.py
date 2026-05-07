"""Maintain a separate module for the version to avoid circular imports."""

import importlib.metadata


__all__ = ["__version__"]

__version__: str

try:
    distributions = importlib.metadata.packages_distributions().get(__package__ or __name__)
    __version__ = importlib.metadata.version(distributions[0] if distributions else "ddtrace")
except Exception:
    __version__ = "0.0.0"
