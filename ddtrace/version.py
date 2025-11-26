"""Maintain a separate module for the version to avoid circular imports."""

import importlib.metadata


__all__ = ["__version__"]

__version__: str

try:
    __version__ = importlib.metadata.version(__package__ or __name__)
except importlib.metadata.PackageNotFoundError:
    __version__ = "0.0.0"
