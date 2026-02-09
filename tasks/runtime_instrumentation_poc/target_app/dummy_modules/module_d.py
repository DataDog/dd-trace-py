"""Dummy module D with instrumentable functions and methods."""

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from tasks.runtime_instrumentation_poc.shared.state import SharedState

_shared_state: "SharedState" = None  # type: ignore


def set_shared_state(state: "SharedState") -> None:
    """Set the global shared state reference."""
    global _shared_state
    _shared_state = state


def func_d1() -> None:
    """Dummy function D1."""
    if _shared_state:
        _shared_state.increment_call_count(
            "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_d:func_d1"
        )


def func_d2() -> None:
    """Dummy function D2."""
    if _shared_state:
        _shared_state.increment_call_count(
            "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_d:func_d2"
        )


def func_d3() -> None:
    """Dummy function D3."""
    if _shared_state:
        _shared_state.increment_call_count(
            "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_d:func_d3"
        )


class ClassD1:
    """Dummy class D1."""

    def method_d1(self) -> None:
        """Instance method D1."""
        if _shared_state:
            _shared_state.increment_call_count(
                "tasks.runtime_instrumentation_poc.target_app.dummy_modules.module_d:ClassD1.method_d1"
            )
