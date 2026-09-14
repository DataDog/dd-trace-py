"""Maintain a separate module for the version to avoid circular imports."""

import importlib.metadata


__all__ = ["__version__"]

__version__: str


def _resolve_version() -> str:
    try:
        distributions = importlib.metadata.packages_distributions().get(__package__ or __name__) or []
    except Exception:
        distributions = []

    # packages_distributions() can include unnamed or incomplete distributions
    # ahead of ddtrace in embedded runtimes. Try every candidate and always try
    # the canonical distribution name before falling back.
    for distribution in dict.fromkeys((*distributions, "ddtrace")):
        if not distribution:
            continue
        try:
            resolved = importlib.metadata.version(distribution)
        except Exception:
            continue
        if resolved:
            return resolved
    return "0.0.0"


__version__ = _resolve_version()
