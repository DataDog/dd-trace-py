"""Dummy module A with instrumentable functions and methods.

This module provides test callables for the runtime instrumentation PoC.
Each callable increments its call counter in the shared state.
"""

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from tasks.runtime_instrumentation_poc.shared.state import SharedState

# Global reference to shared state (set during initialization)
_shared_state: "SharedState" = None  # type: ignore


def set_shared_state(state: "SharedState") -> None:
    """Set the global shared state reference.

    Args:
        state: SharedState instance
    """
    global _shared_state
    _shared_state = state


def func_a1() -> None:
    """Dummy function A1."""
    if _shared_state:
        _shared_state.increment_call_count(
            "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_a:func_a1"
        )


def func_a2() -> None:
    """Dummy function A2."""
    if _shared_state:
        _shared_state.increment_call_count(
            "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_a:func_a2"
        )


def func_a3() -> None:
    """Dummy function A3."""
    if _shared_state:
        _shared_state.increment_call_count(
            "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_a:func_a3"
        )


class ClassA1:
    """Dummy class A1 with various method types."""

    def method_a1(self) -> None:
        """Instance method A1."""
        if _shared_state:
            _shared_state.increment_call_count(
                "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_a:ClassA1.method_a1"
            )

    def method_a2(self) -> None:
        """Instance method A2."""
        if _shared_state:
            _shared_state.increment_call_count(
                "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_a:ClassA1.method_a2"
            )

    @staticmethod
    def static_a1() -> None:
        """Static method A1."""
        if _shared_state:
            _shared_state.increment_call_count(
                "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_a:ClassA1.static_a1"
            )

    @classmethod
    def class_a1(cls) -> None:
        """Class method A1."""
        if _shared_state:
            _shared_state.increment_call_count(
                "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_a:ClassA1.class_a1"
            )


class ClassA2:
    """Dummy class A2 with instance methods."""

    def method_a3(self) -> None:
        """Instance method A3."""
        if _shared_state:
            _shared_state.increment_call_count(
                "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_a:ClassA2.method_a3"
            )

    def method_a4(self) -> None:
        """Instance method A4."""
        if _shared_state:
            _shared_state.increment_call_count(
                "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_a:ClassA2.method_a4"
            )
