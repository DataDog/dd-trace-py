"""Dummy module B with instrumentable functions and methods."""

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from tasks.runtime_instrumentation_poc.shared.state import SharedState

_shared_state: "SharedState" = None  # type: ignore


def set_shared_state(state: "SharedState") -> None:
    """Set the global shared state reference."""
    global _shared_state
    _shared_state = state


def func_b1() -> None:
    """Dummy function B1."""
    if _shared_state:
        _shared_state.increment_call_count(
            "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_b:func_b1"
        )


def func_b2() -> None:
    """Dummy function B2."""
    if _shared_state:
        _shared_state.increment_call_count(
            "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_b:func_b2"
        )


class ClassB1:
    """Dummy class B1."""

    def method_b1(self) -> None:
        """Instance method B1."""
        if _shared_state:
            _shared_state.increment_call_count(
                "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_b:ClassB1.method_b1"
            )

    @staticmethod
    def static_b1() -> None:
        """Static method B1."""
        if _shared_state:
            _shared_state.increment_call_count(
                "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_b:ClassB1.static_b1"
            )
