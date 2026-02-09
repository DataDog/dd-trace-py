"""Dummy module C with instrumentable functions and methods."""

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from tasks.runtime_instrumentation_poc.shared.state import SharedState

_shared_state: "SharedState" = None  # type: ignore


def set_shared_state(state: "SharedState") -> None:
    """Set the global shared state reference."""
    global _shared_state
    _shared_state = state


def func_c1() -> None:
    """Dummy function C1."""
    if _shared_state:
        _shared_state.increment_call_count(
            "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_c:func_c1"
        )


def func_c2() -> None:
    """Dummy function C2."""
    if _shared_state:
        _shared_state.increment_call_count(
            "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_c:func_c2"
        )


class ClassC1:
    """Dummy class C1."""

    def method_c1(self) -> None:
        """Instance method C1."""
        if _shared_state:
            _shared_state.increment_call_count(
                "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_c:ClassC1.method_c1"
            )

    @classmethod
    def class_c1(cls) -> None:
        """Class method C1."""
        if _shared_state:
            _shared_state.increment_call_count(
                "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_c:ClassC1.class_c1"
            )
