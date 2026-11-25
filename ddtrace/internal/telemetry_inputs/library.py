"""The library telemetry reports for: its identity, the code it owns and how its integrations are named."""

from typing import Callable
from typing import NamedTuple
from typing import Optional
from typing import Tuple


def _no_integration(filename: str) -> Optional[str]:
    return None


class Library(NamedTuple):
    """The library telemetry reports for."""

    # Distribution name. Telemetry never reports the library as one of its own dependencies.
    name: str
    # Reported as the application's tracer_version.
    version: str
    # Sent with each product change.
    product_version: str
    # Path fragments that mark a file as the library's code, e.g. `ddtrace/`. A file whose name contains one of
    # them is library code. The default is no fragment: no code is attributed to the library.
    code_roots: Tuple[str, ...] = ()  # noqa: UP006
    # Maps the name of a file to the integration it belongs to, or None. The default names no integration.
    integration_of: Callable[[str], Optional[str]] = _no_integration


_library: Optional[Library] = None


def register_library(
    name: str,
    version: str,
    product_version: str,
    code_roots: Tuple[str, ...] = (),  # noqa: UP006
    integration_of: Callable[[str], Optional[str]] = _no_integration,
) -> None:
    """Set the library telemetry reports for. The first registration wins; later ones are ignored."""
    global _library

    if _library is None:
        _library = Library(name, version, product_version, tuple(code_roots), integration_of)


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


def is_library_code(filename: str) -> bool:
    """Return whether ``filename`` is in the code the library registered as its own."""
    return any(root in filename for root in get_library().code_roots)


def get_integration(filename: str) -> Optional[str]:
    """Return the name of the integration the library says ``filename`` belongs to, or None."""
    return get_library().integration_of(filename)
