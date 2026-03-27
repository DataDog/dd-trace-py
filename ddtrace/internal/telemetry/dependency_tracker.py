"""Encapsulates dependency tracking for telemetry reporting.

Owns the set of imported dependencies, SCA metadata flag, and all logic
for discovering new dependencies, attaching reachability metadata, and
producing the ``app-dependencies-loaded`` telemetry payload.

AIDEV-NOTE: Extracted from TelemetryWriter to separate dependency-tracking
concerns from the transport/batching layer.  The writer delegates to a
single DependencyTracker instance.
"""

from importlib.metadata import version as importlib_metadata_version
from threading import Lock
from typing import Any
from typing import Iterable
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.internal.packages import get_module_distribution_versions
from ddtrace.internal.settings._telemetry import config

from . import modules
from .dependency import DependencyEntry
from .dependency import attach_reachability_metadata


log = get_logger(__name__)


class DependencyTracker:
    """Thread-safe tracker for imported dependencies and SCA metadata.

    All mutable access is protected by an internal lock.

    AIDEV-NOTE: The tracker does not check any SCA config flag.  It relies
    on the DependencyEntry.metadata field state:
    - metadata is None  -> entry serialized without "metadata" key
    - metadata is not None -> entry serialized with "metadata" key
    SCA product is responsible for setting metadata to [] on entries
    when it starts (via enable_sca_metadata).
    """

    def __init__(self) -> None:
        self._imported_dependencies: dict[str, DependencyEntry] = {}
        self._sca_metadata_enabled: bool = False
        self._modules_already_imported: set[str] = set()
        self._lock = Lock()

    def update_imported(self, new_modules: Iterable[str]) -> list[dict]:
        """Discover new dependencies from recently imported modules.

        Adds a DependencyEntry for each newly discovered package.
        Returns serialized dependency dicts ready for the telemetry payload.

        Caller must hold self._lock (called internally from collect_report).
        """
        deps: list[dict] = []
        for module_name in new_modules:
            dists = get_module_distribution_versions(module_name)
            if not dists:
                continue

            name, version = dists
            if name == "ddtrace":
                continue
            if name in self._imported_dependencies:
                continue

            metadata = [] if self._sca_metadata_enabled else None
            entry = DependencyEntry(name=name, version=version, metadata=metadata)
            self._imported_dependencies[name] = entry
            deps.append(entry.to_telemetry_dict())
        return deps

    def collect_report(self) -> Optional[list[dict[str, Any]]]:
        """Discover new modules, collect re-reports, mark sent. Return payload or None.

        When a dependency has unsent metadata (attached by the SCA hook),
        it is re-reported with ALL metadata (sent + unsent) per the RFC.
        """
        if not config.DEPENDENCY_COLLECTION:
            return None

        with self._lock:
            newly_imported_deps = modules.get_newly_imported_modules(self._modules_already_imported)
            new_deps = self.update_imported(newly_imported_deps)

            # Mark new deps as initially sent
            for dep_dict in new_deps:
                entry = self._imported_dependencies.get(dep_dict["name"])
                if entry:
                    entry.mark_initial_sent()
                    entry.mark_all_metadata_sent()

            # Collect names of deps just reported above to avoid double-reporting.
            just_reported = {d["name"] for d in new_deps}

            # Re-report deps that need it: auto-created by SCA hook (never
            # reported yet) or already reported but with new unsent metadata.
            # Include ALL metadata (sent + unsent) per RFC.
            re_report_deps: list[dict] = []
            for entry in self._imported_dependencies.values():
                if entry.name in just_reported:
                    continue
                if entry.needs_report():
                    re_report_deps.append(entry.to_telemetry_dict(include_all_metadata=True))
                    entry.mark_initial_sent()
                    entry.mark_all_metadata_sent()

            all_deps = new_deps + re_report_deps
            return all_deps if all_deps else None

    def attach_metadata(
        self,
        package_name: str,
        cve_id: str,
        reached: bool,
        path: str,
        method: str,
        line: int,
    ) -> bool:
        """Attach reachability metadata to an imported dependency.

        Called by SCA detection hook when a vulnerable symbol is reached at runtime.
        Thread-safe: acquires lock before mutating dependency entries.

        If SCA is active and the package isn't tracked yet (telemetry heartbeat
        hasn't discovered it), auto-creates the entry with metadata=[].

        Returns:
            True if metadata was attached, False otherwise.
        """
        with self._lock:
            # Auto-create entry if SCA is active but telemetry hasn't tracked
            # this package yet (timing: hook fires before first heartbeat).
            if package_name not in self._imported_dependencies and self._sca_metadata_enabled:
                try:
                    ver = importlib_metadata_version(package_name)
                except Exception:
                    ver = ""
                self._imported_dependencies[package_name] = DependencyEntry(
                    name=package_name, version=ver, metadata=[]
                )
            return attach_reachability_metadata(
                self._imported_dependencies, package_name, cve_id, reached, path, method, line
            )

    def enable_sca_metadata(self) -> None:
        """Activate SCA metadata on all tracked and future dependencies.

        Called by the SCA product on start.  Sets metadata from None to []
        on all existing entries and sets a flag so new entries created by
        update_imported also get metadata=[].
        """
        with self._lock:
            self._sca_metadata_enabled = True
            for entry in self._imported_dependencies.values():
                if entry.metadata is None:
                    entry.metadata = []

    def get_all_dependencies(self) -> dict[str, DependencyEntry]:
        """Return the dependency dict (for extended heartbeat serialization).

        The caller should hold the writer's service_lock when iterating.
        """
        return self._imported_dependencies

    def reset(self) -> None:
        """Reset all state (used on fork / queue reset)."""
        with self._lock:
            self._imported_dependencies = {}
            self._sca_metadata_enabled = False
            self._modules_already_imported = set()


def update_imported_dependencies(
    already_imported: dict[str, DependencyEntry],
    new_modules: Iterable[str],
    sca_metadata_enabled: bool = False,
) -> list[dict]:
    """Standalone version of dependency discovery for backward compatibility.

    Mutates *already_imported* in place, adding a DependencyEntry for each
    newly discovered package.  Returns the list of serialized dependency
    dicts ready for the ``app-dependencies-loaded`` telemetry payload.

    AIDEV-NOTE: This free function is kept for backward compatibility with
    tests and benchmarks that call it directly.  Production code should use
    DependencyTracker instead.
    """
    deps: list[dict] = []
    for module_name in new_modules:
        dists = get_module_distribution_versions(module_name)
        if not dists:
            continue

        name, version = dists
        if name == "ddtrace":
            continue
        if name in already_imported:
            continue

        metadata = [] if sca_metadata_enabled else None
        entry = DependencyEntry(name=name, version=version, metadata=metadata)
        already_imported[name] = entry
        deps.append(entry.to_telemetry_dict())
    return deps
