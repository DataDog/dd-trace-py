"""Tests for ddtrace.internal.monitoring, the multiplexed sys.monitoring layer.

These focus on the local-vs-global event split for PY_UNWIND. PY_UNWIND is a
global-only sys.monitoring event: passing it to ``set_local_events`` raises
``ValueError: invalid local event set``. The module therefore enables PY_UNWIND
via ``set_events`` (global) and keeps PY_START/PY_RETURN/LINE per-code via
``set_local_events`` (local).
"""

import sys
from types import CodeType
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import Iterator

import pytest


# The module only imports on 3.15+ (it raises ImportError below that). On older
# interpreters importorskip skips the whole module at collection time. Under a
# type checker we import it directly so member/base-class references resolve.
if TYPE_CHECKING:
    from ddtrace.internal import monitoring
else:
    monitoring = pytest.importorskip("ddtrace.internal.monitoring")

# TODO(py-315): the full 3.15 monitoring/profiling stack (PR #17624 and its
# dependencies) is not yet enabled on this branch. Skip on 3.15 for now; the
# stacked PRs remove this mark and these tests run and pass on 3.15.
pytestmark = pytest.mark.skipif(
    sys.version_info >= (3, 15),
    reason="TODO(py-315): enable once the 3.15 monitoring stack lands (PR #17624 + deps)",
)

# Fetched via getattr so the type checker treats it as Any: the source module's
# `_E = sys.monitoring.events` has an indeterminate type when mypy analyzes it
# under a pre-3.15 Python version.
_E: Any = getattr(monitoring, "_E")
_sys_monitoring: Any = getattr(sys, "monitoring", None)


class UnwindHandler(monitoring.MonitoringEventHandler):
    def __init__(self) -> None:
        self.unwinds: list[tuple[CodeType, BaseException]] = []

    def on_py_unwind(self, code: CodeType, instruction_offset: int, exception: BaseException) -> None:
        self.unwinds.append((code, exception))


class StartAndUnwindHandler(monitoring.MonitoringEventHandler):
    def __init__(self) -> None:
        self.started: bool = False
        self.unwound: bool = False

    def on_py_start(self, code: CodeType, instruction_offset: int) -> None:
        self.started = True

    def on_py_unwind(self, code: CodeType, instruction_offset: int, exception: BaseException) -> None:
        self.unwound = True


@pytest.fixture
def registered() -> Iterator[
    Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler]
]:
    """Register a handler for a code object and always unregister afterwards."""
    registrations: list[tuple[CodeType, monitoring.MonitoringEventHandler]] = []

    def _register(code: CodeType, handler: monitoring.MonitoringEventHandler) -> monitoring.MonitoringEventHandler:
        monitoring.register(code, handler)
        registrations.append((code, handler))
        return handler

    yield _register

    for code, handler in registrations:
        monitoring.unregister(code, handler)


def test_register_unwind_handler_does_not_raise(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
) -> None:
    """Regression: registering a PY_UNWIND-only handler must not raise.

    Before the fix, ``register`` passed PY_UNWIND to ``set_local_events`` which
    raised ``ValueError: invalid local event set``.
    """

    def boom() -> None:
        raise ValueError("boom")

    registered(boom.__code__, UnwindHandler())


def test_unwind_enabled_globally_not_locally(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
) -> None:
    """PY_UNWIND must be a global event; it must not appear in local events."""

    def boom() -> None:
        raise ValueError("boom")

    registered(boom.__code__, UnwindHandler())

    tool_id: int | None = monitoring._tool_id
    assert tool_id is not None

    local_events: int = _sys_monitoring.get_local_events(tool_id, boom.__code__)
    global_events: int = _sys_monitoring.get_events(tool_id)

    assert not (local_events & _E.PY_UNWIND), "PY_UNWIND must not be a local event"
    assert global_events & _E.PY_UNWIND, "PY_UNWIND must be enabled globally"


def test_on_py_unwind_does_not_disable_unregistered_code() -> None:
    """Regression: the unwind callback must return None (never DISABLE).

    PY_UNWIND fires for every unwinding frame, including code with no handler.
    Returning DISABLE would permanently disarm the global event for that code
    location with no re-arm path, so unregistered code must yield None.
    """

    def unrelated() -> None:
        pass

    result: object | None = monitoring._on_py_unwind(unrelated.__code__, 0, ValueError("x"))
    assert result is None, "unregistered code must not be disabled (must return None, not DISABLE)"


def test_unwind_callback_fires_on_exception(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
) -> None:
    """A registered handler receives on_py_unwind when its code unwinds."""

    def boom() -> None:
        raise ValueError("kaboom")

    handler: UnwindHandler = registered(boom.__code__, UnwindHandler())  # type: ignore[assignment]

    with pytest.raises(ValueError):
        boom()

    assert any(exc.args == ("kaboom",) for _, exc in handler.unwinds), (
        "on_py_unwind was not called for the unwinding frame"
    )


def test_unregister_disables_global_unwind() -> None:
    """Unregistering the last unwind handler clears the global PY_UNWIND event."""

    def boom() -> None:
        raise ValueError("boom")

    handler: UnwindHandler = UnwindHandler()
    monitoring.register(boom.__code__, handler)

    tool_id: int | None = monitoring._tool_id
    assert tool_id is not None
    assert _sys_monitoring.get_events(tool_id) & _E.PY_UNWIND

    monitoring.unregister(boom.__code__, handler)

    assert not (_sys_monitoring.get_events(tool_id) & _E.PY_UNWIND), (
        "global PY_UNWIND should be disabled once no handlers need it"
    )


def test_mixed_local_and_global_events(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
) -> None:
    """A handler overriding both PY_START and PY_UNWIND gets each at its scope."""

    def fn() -> None:
        raise ValueError("mixed")

    handler: StartAndUnwindHandler = registered(fn.__code__, StartAndUnwindHandler())  # type: ignore[assignment]

    tool_id: int | None = monitoring._tool_id
    assert tool_id is not None

    local_events: int = _sys_monitoring.get_local_events(tool_id, fn.__code__)
    assert local_events & _E.PY_START, "PY_START must be a local event"
    assert not (local_events & _E.PY_UNWIND), "PY_UNWIND must not be local"
    assert _sys_monitoring.get_events(tool_id) & _E.PY_UNWIND, "PY_UNWIND must be global"

    with pytest.raises(ValueError):
        fn()

    assert handler.started, "on_py_start did not fire"
    assert handler.unwound, "on_py_unwind did not fire"
