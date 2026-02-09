"""State registry for SCA instrumentation tracking.

This module provides thread-safe tracking of instrumentation state for all
target functions registered via Remote Configuration.
"""

from dataclasses import dataclass
from dataclasses import field
import os
from threading import Lock
from types import CodeType
from typing import Dict
from typing import Optional

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


@dataclass
class InstrumentationState:
    """State for a single instrumented target.

    Attributes:
        qualified_name: Fully qualified name (e.g., "module.path:Class.method")
        is_instrumented: True if bytecode patching was applied
        is_pending: True if waiting for module import
        original_code: Original __code__ object before patching
        hit_count: Number of times the instrumented function was called
        lock: Thread-safe lock for this state
    """

    qualified_name: str
    is_instrumented: bool = False
    is_pending: bool = False
    original_code: Optional[CodeType] = None
    hit_count: int = 0
    lock: Lock = field(default_factory=Lock)


class InstrumentationRegistry:
    """Thread-safe registry of instrumentation state.

    Manages state for all functions targeted by SCA detection, including:
    - Instrumented functions and their original code
    - Pending functions (modules not yet imported)
    - Hit counts for each instrumented function

    This registry is process-local and does not persist across forks.
    """

    def __init__(self):
        """Initialize empty registry."""
        self._targets: Dict[str, InstrumentationState] = {}
        self._lock = Lock()  # Global registry lock

    def add_target(self, qualified_name: str, pending: bool = False) -> None:
        """Add a new target for tracking.

        Args:
            qualified_name: Fully qualified name (e.g., "module.path:function")
            pending: True if waiting for module import, False if already resolved
        """
        with self._lock:
            if qualified_name not in self._targets:
                self._targets[qualified_name] = InstrumentationState(qualified_name=qualified_name, is_pending=pending)
                log.debug("Registered target: %s (pending=%s)", qualified_name, pending)

    def remove_target(self, qualified_name: str) -> None:
        """Remove a target from tracking.

        Args:
            qualified_name: Fully qualified name to remove
        """
        with self._lock:
            if qualified_name in self._targets:
                self._targets.pop(qualified_name)
                log.debug("Removed target: %s", qualified_name)

    def has_target(self, qualified_name: str) -> bool:
        """Check if target is tracked.

        Args:
            qualified_name: Fully qualified name to check

        Returns:
            True if target exists in registry
        """
        with self._lock:
            return qualified_name in self._targets

    def is_instrumented(self, qualified_name: str) -> bool:
        """Check if target is instrumented.

        Args:
            qualified_name: Fully qualified name to check

        Returns:
            True if target is instrumented (bytecode patched)
        """
        with self._lock:
            state = self._targets.get(qualified_name)
        if state is not None:
            with state.lock:
                return state.is_instrumented
        return False

    def is_pending(self, qualified_name: str) -> bool:
        """Check if target is pending module import.

        Args:
            qualified_name: Fully qualified name to check

        Returns:
            True if target is pending (module not yet imported)
        """
        with self._lock:
            state = self._targets.get(qualified_name)
        if state is not None:
            with state.lock:
                return state.is_pending
        return False

    def mark_instrumented(self, qualified_name: str, original_code: CodeType) -> None:
        """Mark target as instrumented and store original code.

        Args:
            qualified_name: Fully qualified name of instrumented target
            original_code: Original __code__ object before patching
        """
        with self._lock:
            state = self._targets.get(qualified_name)
        if state:
            with state.lock:
                state.is_instrumented = True
                state.is_pending = False
                state.original_code = original_code
                log.debug("Marked as instrumented: %s", qualified_name)

    def record_hit(self, qualified_name: str) -> None:
        """Record a hit on an instrumented function.

        This is called by the SCA detection hook when an instrumented
        function is invoked.

        Args:
            qualified_name: Fully qualified name of the called function
        """
        with self._lock:
            state = self._targets.get(qualified_name)
        if state:
            # No lock needed for simple increment in CPython (GIL-protected)
            state.hit_count += 1

    def get_hit_count(self, qualified_name: str) -> int:
        """Get hit count for a target.

        Args:
            qualified_name: Fully qualified name to check

        Returns:
            Number of times the function was called (0 if not found)
        """
        with self._lock:
            state = self._targets.get(qualified_name)
        if state:
            # Simple read is GIL-protected in CPython
            return state.hit_count
        return 0

    def get_stats(self) -> Dict[str, Dict]:
        """Get statistics for all targets.

        Returns:
            Dict mapping qualified names to their state info:
            {
                "module.path:function": {
                    "is_instrumented": True,
                    "is_pending": False,
                    "hit_count": 42
                },
                ...
            }
        """
        # Quick copy of targets dict while holding lock
        with self._lock:
            targets_copy = dict(self._targets)

        # Now iterate without holding global lock
        stats = {}
        for name, state in targets_copy.items():
            # Simple reads are GIL-protected in CPython
            stats[name] = {
                "is_instrumented": state.is_instrumented,
                "is_pending": state.is_pending,
                "hit_count": state.hit_count,
            }
        return stats

    def clear(self) -> None:
        """Clear all tracked targets.

        This is primarily for testing purposes.
        """
        with self._lock:
            self._targets.clear()
            log.debug("Cleared all targets from registry")


# Global singleton registry
_global_registry: Optional[InstrumentationRegistry] = None
_registry_lock = Lock()


def _reset_global_registry_after_fork():
    """Reset global registry after fork to prevent stale state in child process.

    This ensures child processes don't inherit parent's instrumentation state,
    hit counts, or lock objects which may be in locked state.
    """
    global _global_registry
    _global_registry = None
    log.debug("Reset global InstrumentationRegistry after fork")


# Register fork handler for process safety
if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_global_registry_after_fork)


def get_global_registry() -> InstrumentationRegistry:
    """Get or create global registry instance.

    Returns:
        Global InstrumentationRegistry singleton
    """
    global _global_registry

    with _registry_lock:
        if _global_registry is None:
            _global_registry = InstrumentationRegistry()
            log.debug("Created global InstrumentationRegistry")
        return _global_registry
