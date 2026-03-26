"""State registry for SCA instrumentation tracking.

Provides thread-safe tracking of instrumentation state for all
target functions to be instrumented for SCA detection.
"""

from dataclasses import dataclass
from dataclasses import field
import os
from threading import Lock
from types import CodeType
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


@dataclass
class InstrumentationState:
    """State for a single instrumented target."""

    qualified_name: str
    package_name: str = ""
    cve_ids: List[str] = field(default_factory=list)
    line: int = 0
    is_instrumented: bool = False
    is_pending: bool = False
    original_code: Optional[CodeType] = None
    hit_count: int = 0


class InstrumentationRegistry:
    """Thread-safe registry of instrumentation state.

    All mutable access is protected by a single lock to avoid
    the complexity and overhead of per-entry locks.
    """

    def __init__(self):
        self._targets: Dict[str, InstrumentationState] = {}
        self._lock = Lock()

    def add_target(
        self,
        qualified_name: str,
        pending: bool = False,
        package_name: str = "",
        cve_ids: Optional[List[str]] = None,
        line: int = 0,
    ) -> None:
        with self._lock:
            if qualified_name not in self._targets:
                self._targets[qualified_name] = InstrumentationState(
                    qualified_name=qualified_name,
                    package_name=package_name,
                    cve_ids=cve_ids or [],
                    line=line,
                    is_pending=pending,
                )
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

    def is_pending(self, qualified_name: str) -> bool:
        with self._lock:
            state = self._targets.get(qualified_name)
            return state.is_pending if state else False

    def mark_instrumented(self, qualified_name: str, original_code: CodeType) -> None:
        with self._lock:
            state = self._targets.get(qualified_name)
            if state:
                state.is_instrumented = True
                state.is_pending = False
                state.original_code = original_code
                log.debug("Marked as instrumented: %s", qualified_name)

    def record_hit(self, qualified_name: str) -> None:
        with self._lock:
            state = self._targets.get(qualified_name)
            if state:
                state.hit_count += 1

    def get_hit_count(self, qualified_name: str) -> int:
        with self._lock:
            state = self._targets.get(qualified_name)
            return state.hit_count if state else 0

    def get_target_info(self, qualified_name: str) -> Optional[Dict]:
        """Get vulnerability info for a target (used by the SCA hook).

        Returns a snapshot dict with package_name, cve_ids, line.
        """
        with self._lock:
            state = self._targets.get(qualified_name)
            if state:
                return {
                    "package_name": state.package_name,
                    "cve_ids": list(state.cve_ids),
                    "line": state.line,
                }
        return None

    def get_stats(self) -> Dict[str, Dict]:
        with self._lock:
            return {
                name: {
                    "is_instrumented": state.is_instrumented,
                    "is_pending": state.is_pending,
                    "hit_count": state.hit_count,
                }
                for name, state in self._targets.items()
            }

    def clear(self) -> None:
        with self._lock:
            self._targets.clear()
            log.debug("Cleared all targets from registry")


# Global singleton registry
_global_registry: Optional[InstrumentationRegistry] = None
_registry_lock = Lock()


def _reset_global_registry_after_fork():
    """Reset global registry and lock after fork to prevent stale state and deadlocks."""
    global _global_registry, _registry_lock
    _global_registry = None
    _registry_lock = Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_global_registry_after_fork)


def get_global_registry() -> InstrumentationRegistry:
    """Get or create global registry instance."""
    global _global_registry

    with _registry_lock:
        if _global_registry is None:
            _global_registry = InstrumentationRegistry()
            log.debug("Created global InstrumentationRegistry")
        return _global_registry
