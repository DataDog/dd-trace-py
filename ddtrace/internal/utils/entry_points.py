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

# Every entry point on the path with its distribution, from the first enumeration. The ddtrace_internal groups
# (supported configurations, library, telemetry providers) are read one after another while ddtrace imports, and
# each lookup would otherwise enumerate every installed distribution again, which costs tens of milliseconds with
# a few hundred packages on Python 3.9. Every reader resolves once and distributions are installed before the
# interpreter starts, so sharing the first enumeration changes no result.
_entry_points: "t.Optional[list[tuple[EntryPoint, Distribution]]]" = None


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def distribution_name(dist: "Distribution") -> str:
    """Return the normalized name of ``dist`` (PEP 503), e.g. ``ddtrace-testing``."""
    return _normalize(dist.metadata["Name"] or "")


def _all_entry_points_with_distributions() -> "list[tuple[EntryPoint, Distribution]]":
    global _entry_points

    if _entry_points is None:
        # Imported lazily: IAST drops importlib.metadata after boot, for gevent.
        import importlib.metadata as importlib_metadata

        if _ENTRY_POINT_HAS_DIST:
            found_eps: object = importlib_metadata.entry_points()
            if isinstance(found_eps, dict):
                # Python 3.10 and 3.11 return the dict-like SelectableGroups, whose interface is deprecated;
                # dict.values() reads the underlying dict without the deprecation warning.
                found_eps = (ep for group in dict.values(found_eps) for ep in group)
            _entry_points = [(ep, ep.dist) for ep in t.cast("t.Iterable[EntryPoint]", found_eps) if ep.dist is not None]
        else:
            # No EntryPoint.dist: walk the distributions instead.
            found: list[tuple[EntryPoint, Distribution]] = []
            seen: set[str] = set()
            for dist in importlib_metadata.distributions():
                name = distribution_name(dist)
                if name in seen:
                    continue
                seen.add(name)
                found.extend((ep, dist) for ep in dist.entry_points)
            _entry_points = found
    return _entry_points
def entry_point_module_path(entry_point: "EntryPoint") -> str:
    """Return the module named by ``entry_point``'s value, without its object part (``mod:obj`` → ``mod``)."""
    return entry_point.value.partition(":")[0]


def is_trusted_entry_point(
    entry_point: "EntryPoint",
    dist: "Distribution",
    trusted_distributions: "t.AbstractSet[str]",
    trusted_module_prefixes: "t.AbstractSet[str]",
) -> bool:
    """Return whether ``entry_point`` may be loaded from ``dist``.

    Both the declaring distribution (normalized name in ``trusted_distributions``) and the module the entry
    point resolves into (under one of ``trusted_module_prefixes``) must be trusted. The module-path check is
    the stronger guard: a distribution name is self-declared metadata and can be spoofed, while code outside
    the trusted namespaces cannot be part of those distributions. Use this before ``entry_point.load()``;
    callers decide how to report the refusal.
    """
    module_path = entry_point_module_path(entry_point)
    return distribution_name(dist) in trusted_distributions and any(
        module_path.startswith(prefix) for prefix in trusted_module_prefixes
    )


def entry_points_with_distributions(group: str) -> list[tuple["EntryPoint", "Distribution"]]:
    """Return the entry points of ``group`` with their distributions, in ``sys.path`` order.

    Like ``importlib.metadata.entry_points()``, a distribution installed twice counts once: the first on the path.
    Reads only metadata; no entry point is loaded. The installed entry points are enumerated once and shared by
    every group lookup, whatever its group.
    """
    return [(ep, dist) for ep, dist in _all_entry_points_with_distributions() if ep.group == group]
