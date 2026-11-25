"""The library telemetry reports for."""

from typing import NamedTuple
from typing import Optional


class Library(NamedTuple):
    """The library telemetry reports for."""

    # Distribution name. Telemetry never reports the library as one of its own dependencies.
    name: str
    # Reported as the application's tracer_version.
    version: str
    # Sent with each product change.
    product_version: str


_library: Optional[Library] = None


def register_library(name: str, version: str, product_version: str) -> None:
    """Set the library telemetry reports for. The first registration wins; later ones are ignored."""
    global _library

    if _library is None:
        _library = Library(name, version, product_version)


def get_library() -> Library:
    """Return the registered library.

    Raises ``RuntimeError`` before registration, rather than reporting a wrong or empty version. In dd-trace-py
    this can't happen: ``ddtrace/__init__.py`` registers before anything imports telemetry, whose writer reads
    the library when it is created.
    """
    if _library is None:
        msg = "telemetry needs register_library() to be called before it is used"
        raise RuntimeError(msg)
    return _library
