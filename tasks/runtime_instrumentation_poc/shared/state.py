"""Thread-safe state management for runtime instrumentation PoC.

This module provides the core data structures for tracking instrumentable callables,
their call counts, and instrumentation status.
"""

from dataclasses import dataclass
from dataclasses import field
from threading import Lock
from types import CodeType
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional


@dataclass
class CallableInfo:
    """Metadata and state for a single instrumentable callable.

    Attributes:
        qualified_name: Fully qualified name (e.g., "module.path:Class.method")
        callable_obj: The actual callable object (function or method)
        call_count: Total number of times this callable has been invoked
        instrumentation_count: Number of times instrumentation hook has been hit
        is_instrumented: Whether bytecode has been patched
        original_code: Backup of original __code__ object (for restoration)
        lock: Per-callable lock for thread-safe counter updates
    """

    qualified_name: str
    callable_obj: Callable
    call_count: int = 0
    instrumentation_count: int = 0
    is_instrumented: bool = False
    original_code: Optional[CodeType] = None
    lock: Lock = field(default_factory=Lock)

    def increment_call_count(self) -> None:
        """Increment call counter (thread-safe)."""
        with self.lock:
            self.call_count += 1

    def increment_instrumentation_count(self) -> None:
        """Increment instrumentation counter (thread-safe)."""
        with self.lock:
            self.instrumentation_count += 1


class SharedState:
    """Global registry of all instrumentable callables.

    This class provides thread-safe access to callable metadata and counters.
    It serves as the central coordination point between the target app,
    instrumentation logic, and UI.

    Thread Safety:
        - Uses fine-grained locking (per-callable) to minimize contention
        - Registry modifications use global lock (only during initialization)
        - Snapshot creation copies data to avoid holding locks during serialization
    """

    def __init__(self) -> None:
        """Initialize empty state."""
        self._callables: Dict[str, CallableInfo] = {}
        self._lock = Lock()  # For registry modifications

    def register_callable(self, qualified_name: str, callable_obj: Callable) -> None:
        """Register a new callable for tracking.

        Args:
            qualified_name: Fully qualified name
            callable_obj: The callable object

        Note:
            Should only be called during initialization phase (not thread-safe afterward)
        """
        with self._lock:
            if qualified_name not in self._callables:
                self._callables[qualified_name] = CallableInfo(
                    qualified_name=qualified_name,
                    callable_obj=callable_obj,
                )

    def increment_call_count(self, qualified_name: str) -> None:
        """Increment call counter for a callable.

        Args:
            qualified_name: Name of the callable

        Note:
            Fast path - no global lock, only per-callable lock
        """
        info = self._callables.get(qualified_name)
        if info:
            info.increment_call_count()

    def increment_instrumentation_count(self, qualified_name: str) -> None:
        """Increment instrumentation counter for a callable.

        Args:
            qualified_name: Name of the callable

        Note:
            Called from instrumentation hook injected into bytecode
        """
        info = self._callables.get(qualified_name)
        if info:
            info.increment_instrumentation_count()

    def mark_instrumented(self, qualified_name: str, original_code: CodeType) -> None:
        """Mark callable as instrumented and store original code.

        Args:
            qualified_name: Name of the callable
            original_code: Original __code__ object (for potential restoration)
        """
        info = self._callables.get(qualified_name)
        if info:
            with info.lock:
                info.is_instrumented = True
                info.original_code = original_code

    def get_callable(self, qualified_name: str) -> Optional[CallableInfo]:
        """Retrieve callable info by name.

        Args:
            qualified_name: Name of the callable

        Returns:
            CallableInfo if found, None otherwise
        """
        return self._callables.get(qualified_name)

    def get_snapshot(self) -> List[Dict[str, Any]]:
        """Get a snapshot of all callable states for UI rendering.

        Returns:
            List of dictionaries containing callable state data

        Note:
            Creates a copy to avoid holding locks during UI rendering.
            Suitable for consumption by UI update loops.
        """
        snapshot = []
        for name, info in self._callables.items():
            with info.lock:
                snapshot.append(
                    {
                        "qualified_name": name,
                        "call_count": info.call_count,
                        "instrumentation_count": info.instrumentation_count,
                        "is_instrumented": info.is_instrumented,
                    }
                )
        return snapshot

    def get_all_qualified_names(self) -> List[str]:
        """Get list of all registered callable names.

        Returns:
            List of qualified names
        """
        return list(self._callables.keys())
