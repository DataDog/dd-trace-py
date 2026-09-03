"""Tests for ddtrace.internal.monitoring, the multiplexed sys.monitoring layer.

On Python 3.15+, PY_UNWIND is a per-code event and is enabled via
``set_local_events`` together with PY_START/PY_RETURN/LINE.
"""

import sys
from types import CodeType
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import Iterator
from typing import Protocol
from typing import cast

import pytest


# The module only imports on 3.15+ (it raises ImportError below that). On older
# interpreters importorskip skips the whole module at collection time. Under a
# type checker we import it directly so member/base-class references resolve.
if TYPE_CHECKING:
    from ddtrace.internal import monitoring
else:
    monitoring = pytest.importorskip("ddtrace.internal.monitoring")


class _MonitoringEvents(Protocol):
    """Subset of sys.monitoring.events used by these tests."""

    PY_START: int
    PY_UNWIND: int


# `_E = sys.monitoring.events` has an indeterminate type when mypy analyzes the
# source module under a pre-3.15 Python version.
_E: _MonitoringEvents = cast(_MonitoringEvents, monitoring._E)  # type: ignore[has-type]
_DISABLE: object = cast(object, monitoring._DISABLE)  # type: ignore[has-type]
_sys_monitoring: Any = getattr(sys, "monitoring", None)


class UnwindHandler(monitoring.MonitoringEventHandler):
    def __init__(self) -> None:
        self.unwinds: list[tuple[CodeType, BaseException]] = []

    def on_py_unwind(self, code: CodeType, instruction_offset: int, exception: BaseException) -> None:
        self.unwinds.append((code, exception))


class LineHandler(monitoring.MonitoringEventHandler):
    def __init__(self, disable: bool = False) -> None:
        self._disable = disable
        self.lines: list[int] = []

    def on_py_line(self, code: CodeType, line_number: int) -> object | None:
        self.lines.append(line_number)
        return _DISABLE if self._disable else None


class RaisingLineHandler(monitoring.MonitoringEventHandler):
    def on_py_line(self, code: CodeType, line_number: int) -> object | None:
        raise RuntimeError("line handler exploded")


class StartAndUnwindHandler(monitoring.MonitoringEventHandler):
    def __init__(self) -> None:
        self.started: bool = False
        self.unwound: bool = False

    def on_py_start(self, code: CodeType, instruction_offset: int) -> None:
        self.started = True

    def on_py_unwind(self, code: CodeType, instruction_offset: int, exception: BaseException) -> None:
        self.unwound = True


class RaisingStartHandler(monitoring.MonitoringEventHandler):
    def __init__(self) -> None:
        self.called: bool = False

    def on_py_start(self, code: CodeType, instruction_offset: int) -> None:
        self.called = True
        raise RuntimeError("start handler exploded")


class RaisingReturnHandler(monitoring.MonitoringEventHandler):
    def __init__(self) -> None:
        self.called: bool = False

    def on_py_return(self, code: CodeType, instruction_offset: int, retval: object) -> None:
        self.called = True
        raise RuntimeError("return handler exploded")


class RaisingUnwindHandler(monitoring.MonitoringEventHandler):
    def __init__(self) -> None:
        self.called: bool = False

    def on_py_unwind(self, code: CodeType, instruction_offset: int, exception: BaseException) -> None:
        self.called = True
        raise RuntimeError("unwind handler exploded")


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
    """Registering a PY_UNWIND-only handler enables the per-code unwind event."""

    def boom() -> None:
        raise ValueError("boom")

    registered(boom.__code__, UnwindHandler())


def test_unwind_enabled_locally(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
) -> None:
    """PY_UNWIND is enabled per code object on Python 3.15+."""

    def boom() -> None:
        raise ValueError("boom")

    registered(boom.__code__, UnwindHandler())

    tool_id: int | None = monitoring._tool_id
    assert tool_id is not None

    local_events: int = _sys_monitoring.get_local_events(tool_id, boom.__code__)
    global_events: int = _sys_monitoring.get_events(tool_id)

    assert local_events & _E.PY_UNWIND, "PY_UNWIND must be a local event on 3.15+"
    assert not (global_events & _E.PY_UNWIND), "PY_UNWIND must not be enabled globally"


def test_on_py_unwind_disables_unregistered_code() -> None:
    """The unwind callback returns DISABLE when no handler is registered."""

    def unrelated() -> None:
        pass

    result: object | None = monitoring._on_py_unwind(unrelated.__code__, 0, ValueError("x"))
    assert result is _DISABLE


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


def test_unregister_clears_local_unwind() -> None:
    """Unregistering the last unwind handler clears the per-code PY_UNWIND event."""

    def boom() -> None:
        raise ValueError("boom")

    handler: UnwindHandler = UnwindHandler()
    monitoring.register(boom.__code__, handler)

    tool_id: int | None = monitoring._tool_id
    assert tool_id is not None
    assert _sys_monitoring.get_local_events(tool_id, boom.__code__) & _E.PY_UNWIND

    monitoring.unregister(boom.__code__, handler)

    assert not (_sys_monitoring.get_local_events(tool_id, boom.__code__) & _E.PY_UNWIND), (
        "local PY_UNWIND should be disabled once no handlers need it"
    )


def test_mixed_local_events(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
) -> None:
    """A handler overriding both PY_START and PY_UNWIND gets each as local events."""

    def fn() -> None:
        raise ValueError("mixed")

    handler: StartAndUnwindHandler = registered(fn.__code__, StartAndUnwindHandler())  # type: ignore[assignment]

    tool_id: int | None = monitoring._tool_id
    assert tool_id is not None

    local_events: int = _sys_monitoring.get_local_events(tool_id, fn.__code__)
    assert local_events & _E.PY_START, "PY_START must be a local event"
    assert local_events & _E.PY_UNWIND, "PY_UNWIND must be a local event on 3.15+"
    assert not (_sys_monitoring.get_events(tool_id) & _E.PY_UNWIND), "PY_UNWIND must not be global"

    with pytest.raises(ValueError):
        fn()

    assert handler.started, "on_py_start did not fire"
    assert handler.unwound, "on_py_unwind did not fire"


def test_on_py_line_disables_when_all_handlers_return_disable(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
) -> None:
    """DISABLE is forwarded to CPython when every LINE handler for the code returns it."""

    def fn() -> None:
        pass

    registered(fn.__code__, LineHandler(disable=True))

    result: object | None = monitoring._on_py_line(fn.__code__, fn.__code__.co_firstlineno)
    assert result is _DISABLE


def test_on_py_line_continues_when_any_handler_declines_disable(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
) -> None:
    """LINE events continue if any registered handler returns something other than DISABLE."""

    def fn() -> None:
        pass

    registered(fn.__code__, LineHandler(disable=True))
    registered(fn.__code__, LineHandler(disable=False))

    result: object | None = monitoring._on_py_line(fn.__code__, fn.__code__.co_firstlineno)
    assert result is not _DISABLE


def test_on_py_line_does_not_disable_when_handler_raises(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
) -> None:
    """A LINE handler that raises must not be treated as a vote to disable."""

    def fn() -> None:
        pass

    registered(fn.__code__, RaisingLineHandler())

    result: object | None = monitoring._on_py_line(fn.__code__, fn.__code__.co_firstlineno)
    assert result is not _DISABLE


def test_on_py_start_propagates_exception(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
) -> None:
    """A PY_START handler's exception is never caught -- it always reaches the caller."""

    def fn() -> None:
        pass

    handler: RaisingStartHandler = registered(fn.__code__, RaisingStartHandler())  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="start handler exploded"):
        monitoring._on_py_start(fn.__code__, 0)

    assert handler.called


def test_on_py_start_propagation_aborts_the_monitored_call(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
) -> None:
    """A propagating PY_START failure must prevent the monitored function body from running."""

    ran: bool = False

    def fn() -> None:
        nonlocal ran
        ran = True

    registered(fn.__code__, RaisingStartHandler())

    with pytest.raises(RuntimeError, match="start handler exploded"):
        fn()

    assert not ran, "the function body must not run when a propagating PY_START handler raises"


def test_on_py_return_propagates_exception(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
) -> None:
    """A PY_RETURN handler's exception is never caught -- it always reaches the caller."""

    def fn() -> None:
        pass

    handler: RaisingReturnHandler = registered(fn.__code__, RaisingReturnHandler())  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="return handler exploded"):
        monitoring._on_py_return(fn.__code__, 0, None)

    assert handler.called


def test_on_py_unwind_propagates_exception(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
) -> None:
    """A PY_UNWIND handler's exception is never caught -- it always reaches the caller."""

    def fn() -> None:
        pass

    handler: RaisingUnwindHandler = registered(fn.__code__, RaisingUnwindHandler())  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="unwind handler exploded"):
        monitoring._on_py_unwind(fn.__code__, 0, ValueError("original"))

    assert handler.called


def test_propagating_handler_skips_later_handlers_for_same_event(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
) -> None:
    """A propagating handler's exception skips any sibling handler registered after it."""

    def fn() -> None:
        pass

    raiser: RaisingStartHandler = registered(fn.__code__, RaisingStartHandler())  # type: ignore[assignment]
    sibling: StartAndUnwindHandler = registered(fn.__code__, StartAndUnwindHandler())  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="start handler exploded"):
        monitoring._on_py_start(fn.__code__, 0)

    assert raiser.called
    assert not sibling.started, "a sibling handler after a propagating raiser must not run"
