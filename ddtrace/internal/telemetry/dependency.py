"""Shared dependency model for telemetry reporting.

Provides DependencyEntry and ReachabilityMetadata dataclasses used by
both the telemetry writer and SCA runtime reachability to track
dependencies and their associated vulnerability metadata.
"""

from dataclasses import dataclass
from dataclasses import field
import json
import logging
from typing import Any
from typing import Optional


log = logging.getLogger(__name__)


@dataclass
class ReachabilityMetadata:
    """A single reachability finding for a dependency.

    Attributes:
        type: Metadata type, always "reachability".
        value: Dict with keys: id, reached, path, method, line.
    """

    type: str
    value: dict
    _sent: bool = field(default=False, repr=False, compare=False)

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

    Used as the value type in TelemetryWriter._imported_dependencies
    when SCA runtime reachability is active.  Both telemetry and SCA can
    interact with this model:
    - Telemetry creates entries on first import (no metadata).
    - SCA attaches metadata when a vulnerable symbol executes.
    - Telemetry re-reports the dependency when unsent metadata exists.

    AIDEV-TODO: mark_initial_sent(), to_telemetry_dict(), and the
    metadata re-reporting logic are intentionally unused in the writer
    until the follow-up SCA PR wires them behind DD_APPSEC_SCA_ENABLED.

    Attributes:
        name: Distribution/package name.
        version: Package version string.
        metadata: Optional list of reachability metadata entries.
            None when no metadata has been attached (the common case),
            avoiding the cost of an empty list per entry.
    """

    name: str
    version: str
    # AIDEV-NOTE: metadata is None (not []) by default to avoid allocating an
    # empty list for every dependency. Most deps never receive metadata.
    metadata: Optional[list[ReachabilityMetadata]] = None
    _initial_report_sent: bool = field(default=False, repr=False, compare=False)

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

    def add_metadata(self, meta: ReachabilityMetadata) -> bool:
        """Add metadata entry, deduplicating by CVE id.

        Returns:
            True if the metadata was added, False if it was a duplicate or
            had no CVE id.
        """
        cve_id = meta.value.get("id")
        if cve_id is None:
            log.debug("Ignoring reachability metadata with no CVE id for %s", self.name)
            return False
        if self.metadata is None:
            self.metadata = []
        for existing in self.metadata:
            if existing.value.get("id") == cve_id:
                return False
        self.metadata.append(meta)
        return True

    def to_telemetry_dict(self, include_all_metadata: bool = False) -> dict[str, Any]:
        """Serialize for the telemetry wire format.

        Args:
            include_all_metadata: If True, include all metadata (for extended heartbeat).
                If False, include only unsent metadata (for periodic reporting).

        Returns:
            Dict compatible with the app-dependencies-loaded payload.
            The metadata key is omitted when there are no entries to include.
        """
        result: dict[str, Any] = {"name": self.name, "version": self.version}
        if self.metadata:
            entries = self.metadata if include_all_metadata else self.get_unsent_metadata()
            if entries:
                result["metadata"] = [m.to_telemetry_dict() for m in entries]
        return result


def attach_reachability_metadata(
    imported_dependencies: dict[str, DependencyEntry],
    package_name: str,
    cve_id: str,
    reached: bool,
    path: str,
    method: str,
    line: int,
) -> bool:
    """Attach reachability metadata to an already-tracked dependency.

    This function is called by SCA when a vulnerable symbol executes.
    The caller must hold the appropriate lock (e.g. TelemetryWriter._service_lock).

    Returns:
        True if metadata was attached, False if the package is not tracked
        or the CVE was already recorded.
    """
    entry = imported_dependencies.get(package_name)
    if entry is None:
        log.debug("Cannot attach metadata: package %r not yet tracked", package_name)
        return False

    meta = ReachabilityMetadata(
        type="reachability",
        value={
            "id": cve_id,
            "reached": reached,
            "path": path,
            "method": method,
            "line": line,
        },
    )
    return entry.add_metadata(meta)
