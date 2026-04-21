"""State registry for SCA instrumentation tracking.

Provides thread-safe tracking of instrumentation state for all
target functions to be instrumented for SCA detection.
"""

from dataclasses import dataclass
from dataclasses import field
from threading import Lock
from types import CodeType
from typing import NamedTuple
from typing import Optional

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class TargetInfo(NamedTuple):
    """Immutable snapshot of vulnerability info for a target.

    Cached at registration time and returned from get_target_info
    without locking or allocation on the hot path.
    """

    package_name: str
    cve_ids: tuple[str, ...]
    line: int


@dataclass
class InstrumentationState:
    """State for a single instrumented target."""

    qualified_name: str
    package_name: str = ""
    cve_ids: list[str] = field(default_factory=list)
    line: int = 0
    is_instrumented: bool = False
    is_pending: bool = False
    original_code: Optional[CodeType] = None
    hit_count: int = 0
    # Cached snapshot built at registration time.  Avoids
    # dict allocation + list copy on every hook invocation (hot path).
    cached_info: Optional[TargetInfo] = field(default=None, repr=False, compare=False)


class InstrumentationRegistry:
    """Thread-safe registry of instrumentation state.

    All mutable access is protected by a single lock to avoid
    the complexity and overhead of per-entry locks.

    record_hit and get_target_info are lock-free because they
    run on the hot path (every instrumented function call).  dict.get is
    GIL-safe for reference reads, hit_count is diagnostic-only (a rare
    lost increment is acceptable), and cached_info is immutable after
    registration.
    """

    def __init__(self) -> None:
        self._targets: dict[str, InstrumentationState] = {}
        self._lock = Lock()

    def add_target(
        self,
        qualified_name: str,
        pending: bool = False,
        package_name: str = "",
        cve_ids: Optional[list[str]] = None,
        line: int = 0,
    ) -> None:
        with self._lock:
            if qualified_name not in self._targets:
                ids = cve_ids or []
                state = InstrumentationState(
                    qualified_name=qualified_name,
                    package_name=package_name,
                    cve_ids=ids,
                    line=line,
                    is_pending=pending,
                    cached_info=TargetInfo(
                        package_name=package_name,
                        cve_ids=tuple(ids),
                        line=line,
                    ),
                )
                self._targets[qualified_name] = state
                log.debug("Registered target: %s (pending=%s)", qualified_name, pending)

    def remove_target(self, qualified_name: str) -> None:
        with self._lock:
            if qualified_name in self._targets:
                del self._targets[qualified_name]
                log.debug("Removed target: %s", qualified_name)

    def has_target(self, qualified_name: str) -> bool:
        with self._lock:
            return qualified_name in self._targets

    def is_instrumented(self, qualified_name: str) -> bool:
        with self._lock:
            state = self._targets.get(qualified_name)
            return state.is_instrumented if state else False

    def mark_instrumented(self, qualified_name: str, original_code: CodeType) -> None:
        with self._lock:
            state = self._targets.get(qualified_name)
            if state:
                state.is_instrumented = True
                state.is_pending = False
                state.original_code = original_code
                log.debug("Marked as instrumented: %s", qualified_name)

    def record_hit(self, qualified_name: str) -> None:
        # No lock: hit_count is diagnostic only; GIL makes dict.get safe
        # and a rare lost increment on hit_count is acceptable.
        state = self._targets.get(qualified_name)
        if state:
            state.hit_count += 1

    def get_target_info(self, qualified_name: str) -> Optional[TargetInfo]:
        """Get vulnerability info for a target (used by the SCA hook).

        Returns the cached TargetInfo snapshot without locking or allocation.
        """
        # No lock: cached_info is immutable after registration and
        # dict.get is GIL-safe for reference reads.
        state = self._targets.get(qualified_name)
        if state:
            return state.cached_info
        return None

    def merge_cve_ids(self, qualified_name: str, new_cve_ids: list[str]) -> bool:
        """Merge additional CVE IDs into an existing target.

        Rebuilds cached_info so the detection hook sees the updated set.
        Returns True if any new CVEs were actually added.
        """
        with self._lock:
            state = self._targets.get(qualified_name)
            if not state:
                return False
            existing = set(state.cve_ids)
            added = [cve for cve in new_cve_ids if cve not in existing]
            if not added:
                return False
            state.cve_ids.extend(added)
            state.cached_info = TargetInfo(
                package_name=state.package_name,
                cve_ids=tuple(state.cve_ids),
                line=state.line,
            )
            log.debug("Merged %d new CVEs into %s", len(added), qualified_name)
            return True

    def clear(self) -> None:
        with self._lock:
            self._targets.clear()
            log.debug("Cleared all targets from registry")


# Global singleton registry
_global_registry: Optional[InstrumentationRegistry] = None
_registry_lock = Lock()


def _reset_global_registry_after_fork() -> None:
    """Reset global registry and lock after fork to prevent stale state and deadlocks."""
    global _global_registry, _registry_lock
    _global_registry = None
    _registry_lock = Lock()


def get_global_registry() -> InstrumentationRegistry:
    """Get or create global registry instance."""
    global _global_registry

    with _registry_lock:
        if _global_registry is None:
            _global_registry = InstrumentationRegistry()
            log.debug("Created global InstrumentationRegistry")
        return _global_registry
