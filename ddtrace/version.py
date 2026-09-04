"""Maintain a separate module for the version to avoid circular imports."""

import importlib.metadata


_LAZY_EXPORTS = frozenset({"distributions"})
__all__ = ["__version__", *sorted(_LAZY_EXPORTS)]

__version__: str

try:
    __version__ = importlib.metadata.version("ddtrace")
except Exception:
    try:
        distributions = importlib.metadata.packages_distributions().get(__package__ or __name__)
        __version__ = importlib.metadata.version(distributions[0] if distributions else "ddtrace")
    except Exception:
        distributions = None
        __version__ = "0.0.0"


def __getattr__(name: str) -> object:
    if name == "distributions":
        try:
            value = importlib.metadata.packages_distributions().get(__package__ or __name__)
        except Exception:
            value = None
        globals()["distributions"] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
