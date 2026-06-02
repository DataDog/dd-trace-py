"""Shared dependency model for telemetry reporting.

Provides DependencyEntry and ReachabilityMetadata dataclasses used by
both the telemetry writer and SCA runtime reachability to track
dependencies and their associated vulnerability metadata.
"""

from dataclasses import dataclass
from dataclasses import field
import json
from typing import Any
from typing import Optional

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


@dataclass
class ReachabilityMetadata:
    """A single reachability finding for a dependency (one per CVE).

    Attributes:
        type: Metadata type, always "reachability".
        value: Dict with keys: id, reached (list of hit objects).
    """

    type: str
    value: dict
    _sent: bool = field(default=False, repr=False, compare=False)

    _MAX_REACHED_ENTRIES = 1

    @property
    def cve_id(self) -> Optional[str]:
        return self.value.get("id")

    def add_reached_entry(self, path: str, symbol: str, line: int) -> bool:
        """Add a hit entry to the reached list.

        Returns True if the entry was added, False if the list is already
        at capacity (max 1 per RFC — first hit wins).
        """
        reached = self.value["reached"]  # always initialized at construction
        if len(reached) >= self._MAX_REACHED_ENTRIES:
            return False
        reached.append({"path": path, "symbol": symbol, "line": line})
        self._sent = False
        return True

    def to_telemetry_dict(self) -> dict:
        """Serialize for the telemetry wire format.

        The value field is JSON-stringified per the telemetry contract.
        """
        try:
            serialized_value = json.dumps(self.value, separators=(",", ":"))
        except (TypeError, ValueError):
            log.debug("Failed to serialize reachability metadata value: %s", self.value)
            serialized_value = "{}"
        return {"type": self.type, "value": serialized_value}

    def _mark_sent(self) -> None:
        """Mark this metadata entry as sent. Internal use only.

        Use DependencyEntry.mark_all_metadata_sent() to mark entries and
        keep the parent's bookkeeping consistent.
        """
        self._sent = True

    @property
    def is_sent(self) -> bool:
        return self._sent


@dataclass
class DependencyEntry:
    """Tracks a dependency and its optional reachability metadata.

    Used as the value type in TelemetryWriter._imported_dependencies.
    Both telemetry and SCA interact with this model:
    - Telemetry creates entries on first import.
    - SCA sets metadata to [] when enabled (signals SCA is active).
    - SCA attaches metadata when a vulnerable symbol executes.
    - Telemetry re-reports the dependency when unsent metadata exists.

    The metadata field drives the wire format:
    - metadata is None  → SCA disabled → no "metadata" key in payload
    - metadata is []    → SCA enabled, no findings → "metadata": []
    - metadata has items → SCA enabled with findings → "metadata": [...]

    Attributes:
        name: Distribution/package name.
        version: Package version string.
        metadata: None when SCA is not active, list (possibly empty) when SCA
            is active.  The None-vs-list distinction drives wire format.
    """

    name: str
    version: str
    # NOTE: metadata is None (not []) by default — SCA product sets it
    # to [] when enabled.  None means SCA is inactive for this entry.
    metadata: Optional[list[ReachabilityMetadata]] = None
    _initial_report_sent: bool = field(default=False, repr=False, compare=False)

    _MAX_METADATA_ENTRIES = 100

    def has_unsent_metadata(self) -> bool:
        """True if any metadata entry has not been sent yet."""
        if self.metadata is None:
            return False
        return any(not m.is_sent for m in self.metadata)

    def needs_report(self) -> bool:
        """True if this dependency has never been reported, or has new unsent metadata."""
        return not self._initial_report_sent or self.has_unsent_metadata()

    def get_unsent_metadata(self) -> list[ReachabilityMetadata]:
        if self.metadata is None:
            return []
        return [m for m in self.metadata if not m.is_sent]

    def mark_initial_sent(self) -> None:
        self._initial_report_sent = True

    def mark_all_metadata_sent(self) -> None:
        if self.metadata is None:
            return
        for m in self.metadata:
            m._mark_sent()

    def add_metadata(self, cve_id: str, path: str = "", symbol: str = "", line: int = 0) -> bool:
        """Add or update reachability metadata for a CVE.

        If the CVE exists with reached=[], add the call-site entry.
        If the CVE doesn't exist, create a new metadata entry.

        Args:
            cve_id: CVE identifier (required).
            path: Caller file path (empty for registration-only).
            symbol: Caller symbol name (empty for registration-only).
            line: Caller line number (0 for registration-only).

        Returns:
            True if metadata was added or updated, False if duplicate/capped.
        """
        if not cve_id:
            log.debug("Ignoring reachability metadata with no CVE id for %s", self.name)
            return False
        if self.metadata is None:
            self.metadata = []

        # Look for existing metadata entry for this CVE
        for existing in self.metadata:
            if existing.cve_id == cve_id:
                # CVE already registered — add hit if call-site info provided
                if path and existing.add_reached_entry(path, symbol, line):
                    return True
                return False

        if len(self.metadata) >= self._MAX_METADATA_ENTRIES:
            return False

        # Create new metadata entry for this CVE
        reached = [{"path": path, "symbol": symbol, "line": line}] if path else []
        meta = ReachabilityMetadata(
            type="reachability",
            value={"id": cve_id, "reached": reached},
        )
        self.metadata.append(meta)
        return True

    def to_telemetry_dict(self, include_all_metadata: bool = False) -> dict[str, Any]:
        """Serialize for the telemetry wire format.

        The metadata key presence is driven by the metadata field state:
        - metadata is None → no "metadata" key (SCA not active)
        - metadata is not None → "metadata" key included (SCA active)

        Args:
            include_all_metadata: If True, include all metadata (for extended
                heartbeat / re-report).  If False, include only unsent.

        Returns:
            Dict compatible with the app-dependencies-loaded payload.
        """
        result: dict[str, Any] = {"name": self.name, "version": self.version}
        if self.metadata is not None:
            entries = self.metadata if include_all_metadata else self.get_unsent_metadata()
            result["metadata"] = [m.to_telemetry_dict() for m in entries] if entries else []
        return result


def attach_reachability_metadata(
    imported_dependencies: dict[str, DependencyEntry],
    package_name: str,
    cve_id: str,
    path: str,
    symbol: str,
    line: int,
) -> bool:
    """Attach reachability metadata to an already-tracked dependency.

    This function is called by SCA when a vulnerable symbol executes.
    The caller must hold the appropriate lock (e.g. TelemetryWriter._service_lock).

    Returns:
        True if metadata was attached, False if the package is not tracked
        or the exact finding was already recorded.
    """
    entry = imported_dependencies.get(package_name)
    if entry is None:
        log.debug("Cannot attach metadata: package %r not yet tracked", package_name)
        return False

    return entry.add_metadata(cve_id, path, symbol, line)


def register_cve_metadata(
    imported_dependencies: dict[str, DependencyEntry],
    package_name: str,
    cve_id: str,
) -> bool:
    """Register a CVE on a dependency with reached=[].

    Called at CVE load time to report known vulnerabilities before any
    symbol is actually executed.  The backend sees the CVE with an empty
    reached list, meaning "known but not yet hit".

    Returns:
        True if the CVE was registered, False if the package is not tracked
        or the CVE was already registered.
    """
    entry = imported_dependencies.get(package_name)
    if entry is None:
        log.debug("Cannot register CVE metadata: package %r not yet tracked", package_name)
        return False

    return entry.add_metadata(cve_id)
