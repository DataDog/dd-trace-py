"""Encapsulates dependency tracking for telemetry reporting.

Owns the set of imported dependencies, SCA metadata flag, and all logic
for discovering new dependencies, attaching reachability metadata, and
producing the ``app-dependencies-loaded`` telemetry payload.

Extracted from TelemetryWriter to separate dependency-tracking
concerns from the transport/batching layer.  The writer delegates to a
single DependencyTracker instance.
"""

from importlib.metadata import PackageNotFoundError
import re
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
from .dependency import register_cve_metadata


log = get_logger(__name__)

_NORMALIZE_RE = re.compile(r"[-_.]+")


def _normalize_dep_name(name: str) -> str:
    """PEP 503 package name canonicalization for consistent dict lookups.

    Distribution metadata may use original casing (e.g. "PyYAML") while
    SCA CVE data uses lowercased names (e.g. "pyyaml").  Normalizing keys
    prevents duplicate entries and lookup misses.
    """
    return _NORMALIZE_RE.sub("-", name).lower()


class DependencyTracker:
    """Thread-safe tracker for imported dependencies and SCA metadata.

    All mutable access is protected by an internal lock.

    SCA-enabled state is read from ``tracer_config._sca_enabled`` so
    it reacts dynamically to Remote Configuration changes instead of
    relying on a one-time snapshot.  The DependencyEntry.metadata field
    state drives the wire format:
    - metadata is None  -> entry serialized without "metadata" key
    - metadata is not None -> entry serialized with "metadata" key
    SCA product is responsible for setting metadata to [] on existing
    entries when it starts (via enable_sca_metadata).
    """

    def __init__(self) -> None:
        self._imported_dependencies: dict[str, DependencyEntry] = {}
        self._modules_already_imported: set[str] = set()
        self._lock = Lock()

    def collect_report(self) -> Optional[list[dict[str, Any]]]:
        """Discover new modules, collect re-reports, mark sent. Return payload or None.

        When a dependency has unsent metadata (attached by the SCA hook),
        it is re-reported with ALL metadata (sent + unsent) per the RFC.
        """
        if not config.DEPENDENCY_COLLECTION:
            return None

        with self._lock:
            newly_imported_deps = modules.get_newly_imported_modules(self._modules_already_imported)
            new_deps = update_imported_dependencies(self._imported_dependencies, newly_imported_deps)

            # Normalize once; reuse the set for sent-marking and re-report dedup.
            new_keys = {_normalize_dep_name(d["name"]) for d in new_deps}
            self._mark_sent(new_keys)

            # Skip the re-report scan when SCA is disabled.
            # Without SCA, no entry will ever have unsent metadata, so the
            # scan over all _imported_dependencies is pure overhead (~887us
            # at 10K deps).  Only entries created by the SCA hook or with
            # metadata attached can trigger needs_report() after initial send.
            from ddtrace.internal.settings._config import config as tracer_config

            if not tracer_config._sca_enabled:
                return new_deps if new_deps else None

            re_report_deps = self._collect_rereports(new_keys)
            all_deps = new_deps + re_report_deps
            return all_deps if all_deps else None

    def _mark_sent(self, keys: Iterable[str]) -> None:
        """Mark entries at ``keys`` as initially sent with all metadata sent.

        Caller must hold self._lock.
        """
        for key in keys:
            entry = self._imported_dependencies.get(key)
            if entry is not None:
                entry.mark_initial_sent()
                entry.mark_all_metadata_sent()

    def _collect_rereports(self, skip_keys: set[str]) -> list[dict[str, Any]]:
        """Return serialized dicts for entries needing a re-report.

        Re-report deps that need it: auto-created by SCA hook (never reported
        yet) or already reported but with new unsent metadata. Include ALL
        metadata (sent + unsent) per RFC.

        Caller must hold self._lock.
        """
        re_report: list[dict[str, Any]] = []
        for key, entry in self._imported_dependencies.items():
            if key in skip_keys:
                continue
            if entry.needs_report():
                re_report.append(entry.to_telemetry_dict(include_all_metadata=True))
                entry.mark_initial_sent()
                entry.mark_all_metadata_sent()
        return re_report

    def snapshot_for_heartbeat(self) -> list[dict[str, Any]]:
        """Return serialized dependency dicts for the extended heartbeat payload.

        Serialization happens under the lock so that concurrent SCA mutations
        (``attach_metadata`` / ``register_cve``) cannot race the iteration of
        ``entry.metadata`` or the ``reached`` list inside ``json.dumps``. The
        payload is a list of fresh dicts safe to hand off to the transport.
        """
        with self._lock:
            return [
                entry.to_telemetry_dict(include_all_metadata=True) for entry in self._imported_dependencies.values()
            ]

    def _ensure_entry(self, package_name: str) -> None:
        """Auto-create a DependencyEntry if SCA is active and package not yet tracked.

        Caller must hold self._lock.
        """
        from ddtrace.internal.settings._config import config as tracer_config

        key = _normalize_dep_name(package_name)
        if key not in self._imported_dependencies and tracer_config._sca_enabled:
            try:
                from importlib.metadata import version as importlib_metadata_version

                version = importlib_metadata_version(package_name)
            except PackageNotFoundError:
                log.debug("Package %r not found in installed metadata", package_name)
                version = ""
            self._imported_dependencies[key] = DependencyEntry(name=package_name, version=version, metadata=[])

    def attach_metadata(
        self,
        package_name: str,
        cve_id: str,
        path: str,
        symbol: str,
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
        key = _normalize_dep_name(package_name)
        with self._lock:
            self._ensure_entry(package_name)
            return attach_reachability_metadata(self._imported_dependencies, key, cve_id, path, symbol, line)

    def register_cve(self, package_name: str, cve_id: str) -> bool:
        """Register a CVE on a dependency with reached=[].

        Called at CVE load time to report known vulnerabilities before any
        symbol is actually executed.  Thread-safe.

        If SCA is active and the package isn't tracked yet, auto-creates the entry.

        Returns:
            True if the CVE was registered, False otherwise.
        """
        key = _normalize_dep_name(package_name)
        with self._lock:
            self._ensure_entry(package_name)
            return register_cve_metadata(self._imported_dependencies, key, cve_id)

    def enable_sca_metadata(self) -> None:
        """Activate SCA metadata on all currently tracked dependencies.

        Called by the SCA product on start.  Sets metadata from None to []
        on all existing entries so the wire format includes the "metadata"
        key.  Future entries pick up the flag from tracer_config._sca_enabled.
        """
        with self._lock:
            for entry in self._imported_dependencies.values():
                if entry.metadata is None:
                    entry.metadata = []

    def reset(self) -> None:
        """Reset all state (used on fork / queue reset)."""
        with self._lock:
            self._imported_dependencies = {}
            self._modules_already_imported = set()


def update_imported_dependencies(
    already_imported: dict[str, DependencyEntry],
    new_modules: Iterable[str],
) -> list[dict]:
    """Standalone version of dependency discovery for backward compatibility.

    Mutates *already_imported* in place, adding a DependencyEntry for each
    newly discovered package.  Returns the list of serialized dependency
    dicts ready for the ``app-dependencies-loaded`` telemetry payload.

    SCA-enabled state is read from ``tracer_config._sca_enabled`` so it
    reacts dynamically to Remote Configuration changes.

    NOTE: This function is kept for backward compatibility with
    tests and benchmarks that call it directly.  Production code should use
    DependencyTracker instead.

    Defensive: on interpreter shutdown or partial teardown, ``importlib.metadata``
    and ``sys.path`` resolution can fail, and even the ``tracer_config`` import
    itself may raise once ``sys.modules`` starts being torn down.  Any exception
    is swallowed so the telemetry path never propagates to ``sys.excepthook``.
    """
    try:
        from ddtrace.internal.settings._config import config as tracer_config

        sca_enabled = tracer_config._sca_enabled
    except Exception:
        log.debug("update_imported_dependencies: failed to read tracer config", exc_info=True)
        return []

    deps: list[dict] = []
    for module_name in new_modules:
        try:
            dists = get_module_distribution_versions(module_name)
            if not dists:
                continue

            name, version = dists
            key = _normalize_dep_name(name)
            if key == "ddtrace" or key in already_imported:
                continue

            metadata: Optional[list] = [] if sca_enabled else None
            entry = DependencyEntry(name=name, version=version, metadata=metadata)
            already_imported[key] = entry
            deps.append(entry.to_telemetry_dict())
        except Exception:
            log.debug("update_imported_dependencies: failed for %r", module_name, exc_info=True)
            continue
    return deps
