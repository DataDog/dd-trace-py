"""Bytecode instrumentation using dd-trace-py's inject_hook infrastructure.

This module provides the core instrumentation logic, patching function bytecode
at runtime to inject instrumentation hooks.
"""

import logging
from types import FunctionType
from typing import TYPE_CHECKING
from typing import Optional
from typing import Tuple

from ddtrace.internal.bytecode_injection import inject_hook


if TYPE_CHECKING:
    from tasks.runtime_instrumentation_poc.shared.state import SharedState


logger = logging.getLogger(__name__)


# Global reference to shared state for the instrumentation hook
_shared_state: Optional["SharedState"] = None


def instrumentation_hook(qualified_name: str) -> None:
    """Hook function injected into bytecode.

    This function is called at the entry of instrumented callables.
    It increments the instrumentation counter in the shared state.

    Args:
        qualified_name: Fully qualified name of the instrumented callable
    """
    if _shared_state:
        _shared_state.increment_instrumentation_count(qualified_name)


class Instrumenter:
    """Handles bytecode instrumentation using dd-trace-py infrastructure.

    Uses the production-tested inject_hook() function from dd-trace-py to
    patch function bytecode at runtime.
    """

    def __init__(self, shared_state: "SharedState") -> None:
        """Initialize instrumenter.

        Args:
            shared_state: Shared state for tracking instrumentation
        """
        self.shared_state = shared_state

        # Set global shared state reference for hook
        global _shared_state
        _shared_state = shared_state

    def instrument_callable(self, qualified_name: str) -> Tuple[bool, str]:
        """Instrument a callable by injecting hook at function entry.

        Args:
            qualified_name: Fully qualified name of callable to instrument

        Returns:
            Tuple of (success, message)
            - success: True if instrumentation succeeded
            - message: Success or error message

        Note:
            Uses dd-trace-py's inject_hook() which handles Python version
            differences and bytecode manipulation details.
        """
        # Get callable info from shared state
        info = self.shared_state.get_callable(qualified_name)
        if info is None:
            return (False, f"Callable not found: {qualified_name}")

        if info.is_instrumented:
            return (False, f"Already instrumented: {qualified_name}")

        callable_obj = info.callable_obj

        # Resolve to underlying function
        func = self._resolve_to_function(callable_obj)
        if func is None:
            return (False, f"Cannot resolve to function: {qualified_name}")

        # Store original code for potential restoration
        original_code = func.__code__

        try:
            # Inject hook at first line using dd-trace-py's inject_hook
            first_line = original_code.co_firstlineno

            logger.debug("Injecting hook into %s at line %s", qualified_name, first_line)

            inject_hook(func, instrumentation_hook, first_line, qualified_name)

            # Mark as instrumented in shared state
            self.shared_state.mark_instrumented(qualified_name, original_code)

            logger.info("Successfully instrumented: %s", qualified_name)
            return (True, f"Successfully instrumented: {qualified_name}")

        except Exception as e:
            error_msg = f"Instrumentation failed for {qualified_name}: {e}"
            logger.error(error_msg)
            return (False, error_msg)

    def _resolve_to_function(self, callable_obj: object) -> Optional[FunctionType]:
        """Resolve various callable types to underlying FunctionType.

        Handles:
        - Functions: direct (already FunctionType)
        - Bound methods: callable_obj.__func__
        - Static methods: already resolved in registry
        - Class methods: already resolved in registry

        Args:
            callable_obj: The callable object to resolve

        Returns:
            FunctionType if successful, None otherwise
        """
        # Already a function
        if isinstance(callable_obj, FunctionType):
            return callable_obj

        # Bound method - get underlying function
        if hasattr(callable_obj, "__func__"):
            underlying = getattr(callable_obj, "__func__")
            if isinstance(underlying, FunctionType):
                return underlying

        logger.warning("Cannot resolve %s (type: %s) to FunctionType", callable_obj, type(callable_obj))
        return None
