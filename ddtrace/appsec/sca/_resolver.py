"""Symbol resolution for SCA detection targets.

This module provides functionality to resolve qualified names (e.g., "module.path:Class.method")
to actual Python callables that can be instrumented.
"""

import importlib
from threading import Lock
from types import FunctionType
from typing import List
from typing import Optional
from typing import Set
from typing import Tuple

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class SymbolResolver:
    """Resolve qualified names to Python callables.

    Supports resolving:
    - Module-level functions: "module.path:function_name"
    - Class methods: "module.path:ClassName.method_name"
    - Static methods: "module.path:ClassName.static_method"
    - Class methods: "module.path:ClassName.class_method"
    """

    @staticmethod
    def resolve(qualified_name: str) -> Optional[Tuple[str, FunctionType]]:
        """Resolve a qualified name to a callable.

        Args:
            qualified_name: Qualified name in format "module.path:symbol" or
                          "module.path:Class.method"

        Returns:
            Tuple of (qualified_name, function) or None if resolution failed

        Examples:
            >>> SymbolResolver.resolve("os.path:join")
            ("os.path:join", <function posixpath.join>)

            >>> SymbolResolver.resolve("flask.app:Flask.route")
            ("flask.app:Flask.route", <function Flask.route>)
        """
        try:
            if ":" not in qualified_name:
                log.debug("Invalid format (missing ':'): %s", qualified_name)
                return None

            module_path, symbol_path = qualified_name.split(":", 1)

            # Import module (may fail if not yet imported)
            try:
                module = importlib.import_module(module_path)
            except ImportError as e:
                log.debug("Module not yet imported: %s (%s)", module_path, e)
                return None

            # Navigate to symbol
            target = module
            for part in symbol_path.split("."):
                try:
                    target = getattr(target, part)
                except AttributeError as e:
                    log.debug("Symbol not found: %s.%s (%s)", module_path, symbol_path, e)
                    return None

            # Validate callable
            if not callable(target):
                log.warning("Target not callable: %s", qualified_name)
                return None

            # Extract function from various callable types
            func = SymbolResolver._extract_function(target)
            if func is None:
                log.warning("Cannot extract function from: %s", qualified_name)
                return None

            log.debug("Resolved: %s -> %s", qualified_name, func)
            return (qualified_name, func)

        except (ValueError, TypeError) as e:
            log.debug("Failed to resolve %s: %s", qualified_name, e)
            return None
        except Exception:
            log.error("Unexpected error resolving %s", qualified_name, exc_info=True)
            return None

    @staticmethod
    def _extract_function(target) -> Optional[FunctionType]:
        """Extract FunctionType from various callable types.

        Handles:
        - Plain functions (FunctionType)
        - Bound/unbound methods (__func__ attribute)
        - Static methods (unwrap via __func__)
        - Class methods (unwrap via __func__)

        Args:
            target: Callable object to extract function from

        Returns:
            FunctionType instance or None if extraction failed
        """
        # Already a function
        if isinstance(target, FunctionType):
            return target

        # Bound or unbound method - extract underlying function
        if hasattr(target, "__func__"):
            func = getattr(target, "__func__")
            if isinstance(func, FunctionType):
                return func

        # Static method or class method - extract via descriptor
        if isinstance(target, (staticmethod, classmethod)):
            func = target.__func__
            if isinstance(func, FunctionType):
                return func

        return None


class LazyResolver:
    """Retry resolution for not-yet-imported modules.

    Maintains a set of qualified names that failed to resolve (typically
    because their modules haven't been imported yet) and provides retry
    functionality. Thread-safe for concurrent access.
    """

    def __init__(self):
        """Initialize empty pending set with lock."""
        self._pending: Set[str] = set()
        self._lock = Lock()

    def add_pending(self, qualified_name: str) -> None:
        """Add target for lazy resolution.

        Args:
            qualified_name: Qualified name that failed to resolve
        """
        with self._lock:
            self._pending.add(qualified_name)
        log.debug("Added to pending resolution: %s", qualified_name)

    def remove_pending(self, qualified_name: str) -> None:
        """Remove target from pending set.

        Args:
            qualified_name: Qualified name to remove
        """
        with self._lock:
            self._pending.discard(qualified_name)

    def get_pending(self) -> Set[str]:
        """Get all pending qualified names.

        Returns:
            Set of qualified names awaiting resolution
        """
        with self._lock:
            return self._pending.copy()

    def retry_pending(self) -> List[Tuple[str, FunctionType]]:
        """Retry resolution of all pending targets.

        Attempts to resolve each pending target. Successfully resolved
        targets are removed from the pending set.

        Returns:
            List of successfully resolved (name, function) tuples
        """
        # Get snapshot of pending targets while holding lock
        with self._lock:
            pending_snapshot = self._pending.copy()

        resolved = []
        resolved_names = set()

        for qname in pending_snapshot:
            result = SymbolResolver.resolve(qname)
            if result:
                resolved.append(result)
                resolved_names.add(qname)
                log.debug("Lazy resolution succeeded: %s", qname)

        # Remove resolved targets from pending set (using set difference)
        with self._lock:
            self._pending -= resolved_names

        if resolved:
            with self._lock:
                remaining = len(self._pending)
            log.info("Lazy resolution completed: %d/%d targets", len(resolved), len(resolved) + remaining)

        return resolved

    def clear(self) -> None:
        """Clear all pending targets.

        This is primarily for testing purposes.
        """
        with self._lock:
            self._pending.clear()
        log.debug("Cleared all pending targets")
