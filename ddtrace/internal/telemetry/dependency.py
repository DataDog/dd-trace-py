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
        return {"type": self.type, "value": json.dumps(self.value, separators=(",", ":"))}

    def mark_sent(self) -> None:
        self._sent = True

    @property
    def is_sent(self) -> bool:
        return self._sent


@dataclass
class DependencyEntry:
    """Tracks a dependency and its optional reachability metadata.

    Used as the value type in TelemetryWriter._imported_dependencies.
    Both telemetry and SCA can interact with this model:
    - Telemetry creates entries on first import (no metadata).
    - SCA attaches metadata when a vulnerable symbol executes.
    - Telemetry re-reports the dependency when unsent metadata exists.

    Attributes:
        name: Distribution/package name.
        version: Package version string.
        metadata: Optional list of reachability metadata entries.
            None when no metadata has been attached (the common case),
            avoiding the cost of an empty list per entry.
        _unsent_count: Number of metadata entries not yet sent.
            Tracked as a counter to make has_unsent_metadata() O(1).
    """

    name: str
    version: str
    # AIDEV-NOTE: metadata is None (not []) by default to avoid allocating an
    # empty list for every dependency. Most deps never receive metadata.
    metadata: Optional[list[ReachabilityMetadata]] = None
    _initial_report_sent: bool = field(default=False, repr=False, compare=False)
    _unsent_count: int = field(default=0, repr=False, compare=False)

    def has_unsent_metadata(self) -> bool:
        """True if any metadata entry has not been sent yet. O(1)."""
        return self._unsent_count > 0

    def needs_report(self) -> bool:
        """True if this dependency has never been reported, or has new unsent metadata."""
        return not self._initial_report_sent or self._unsent_count > 0

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
            m.mark_sent()
        self._unsent_count = 0

    def add_metadata(self, meta: ReachabilityMetadata) -> None:
        """Add metadata entry, deduplicating by CVE id."""
        cve_id = meta.value.get("id")
        if self.metadata is None:
            self.metadata = []
        if cve_id is not None:
            for existing in self.metadata:
                if existing.value.get("id") == cve_id:
                    return
        self.metadata.append(meta)
        self._unsent_count += 1

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
        True if metadata was attached, False if the package is not tracked.
    """
    entry = imported_dependencies.get(package_name)
    if entry is None:
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
    entry.add_metadata(meta)
    return True
