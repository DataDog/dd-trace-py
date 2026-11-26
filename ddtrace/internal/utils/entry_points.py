"""Find the entry points of a group, each with the distribution that declares it.

Code moving to ddtrace-internal finds what dd-trace-py provides through entry points: the declaring distribution
names the provider without importing it. This module imports only the standard library, so it is usable from
modules that load before the logger, such as ``settings.env``.
"""

import re
import sys
import typing as t


if t.TYPE_CHECKING:
    from importlib.metadata import Distribution
    from importlib.metadata import EntryPoint


# EntryPoint.dist was added in Python 3.10. Annotated as a plain bool so type checkers keep both paths.
_ENTRY_POINT_HAS_DIST: bool = sys.version_info >= (3, 10)


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def distribution_name(dist: "Distribution") -> str:
    """Return the normalized name of ``dist`` (PEP 503), e.g. ``ddtrace-testing``."""
    return _normalize(dist.metadata["Name"] or "")


def entry_points_with_distributions(group: str) -> list[tuple["EntryPoint", "Distribution"]]:
    """Return the entry points of ``group`` with their distributions, in ``sys.path`` order.

    Like ``importlib.metadata.entry_points()``, a distribution installed twice counts once: the first on the path.
    Reads only metadata; no entry point is loaded.
    """
    # Imported lazily: IAST drops importlib.metadata after boot, for gevent.
    import importlib.metadata as importlib_metadata

    if _ENTRY_POINT_HAS_DIST:
        return [(ep, ep.dist) for ep in importlib_metadata.entry_points(group=group) if ep.dist is not None]

    # No EntryPoint.dist: walk the distributions instead.
    found: list[tuple[EntryPoint, Distribution]] = []
    seen: set[str] = set()
    for dist in importlib_metadata.distributions():
        name = distribution_name(dist)
        if name in seen:
            continue
        seen.add(name)
        found.extend((ep, dist) for ep in dist.entry_points if ep.group == group)
    return found
