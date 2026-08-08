"""Tests for ddtrace.internal.monitoring, the multiplexed sys.monitoring layer.

On Python 3.15+, PY_UNWIND is a per-code event enabled via set_local_events.
On Python 3.12–3.14, PY_UNWIND is global-only and filtered in the callback.
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


# The module only imports on 3.12+ (it raises ImportError below that). On older
# interpreters importorskip skips the whole module at collection time. Under a
# type checker we import it directly so member/base-class references resolve.
if TYPE_CHECKING:
    from ddtrace.internal import monitoring
else:
    monitoring = pytest.importorskip("ddtrace.internal.monitoring")


PY_312_OR_ABOVE = sys.version_info >= (3, 12)
PY_315_OR_ABOVE = sys.version_info >= (3, 15)


class _MonitoringEvents(Protocol):
    """Subset of sys.monitoring.events used by these tests."""

    PY_START: int
    PY_UNWIND: int


# `_E = sys.monitoring.events` has an indeterminate type when mypy analyzes the
# source module under a pre-3.12 Python version.
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
    """Registering a PY_UNWIND-only handler enables the unwind event."""

    def boom() -> None:
        raise ValueError("boom")

    registered(boom.__code__, UnwindHandler())


@pytest.mark.skipif(not PY_315_OR_ABOVE, reason="PY_UNWIND is local-only on Python 3.15+")
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


@pytest.mark.skipif(
    PY_315_OR_ABOVE,
    reason="On 3.12–3.14 PY_UNWIND is global; unregistered unwind must not return DISABLE",
)
def test_unwind_enabled_globally_on_pre_315(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
) -> None:
    """PY_UNWIND is enabled globally on Python 3.12–3.14 when a handler needs it."""

    def boom() -> None:
        raise ValueError("boom")

    registered(boom.__code__, UnwindHandler())

    tool_id: int | None = monitoring._tool_id
    assert tool_id is not None

    local_events: int = _sys_monitoring.get_local_events(tool_id, boom.__code__)
    global_events: int = _sys_monitoring.get_events(tool_id)

    assert not (local_events & _E.PY_UNWIND), "PY_UNWIND must not be a local event before 3.15"
    assert global_events & _E.PY_UNWIND, "PY_UNWIND must be enabled globally"


@pytest.mark.skipif(not PY_315_OR_ABOVE, reason="DISABLE-for-unregistered applies only when PY_UNWIND is local")
def test_on_py_unwind_disables_unregistered_code() -> None:
    """The unwind callback returns DISABLE when no handler is registered (3.15+)."""

    def unrelated() -> None:
        pass

    result: object | None = monitoring._on_py_unwind(unrelated.__code__, 0, ValueError("x"))
    assert result is _DISABLE


@pytest.mark.skipif(
    PY_315_OR_ABOVE,
    reason="On 3.12–3.14 global PY_UNWIND must not DISABLE unregistered code",
)
def test_on_py_unwind_does_not_disable_unregistered_code_pre_315() -> None:
    """Global PY_UNWIND must return None for unregistered code on 3.12–3.14."""

    def unrelated() -> None:
        pass

    result: object | None = monitoring._on_py_unwind(unrelated.__code__, 0, ValueError("x"))
    assert result is None


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


@pytest.mark.skipif(not PY_315_OR_ABOVE, reason="local PY_UNWIND clearing applies on 3.15+")
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


@pytest.mark.skipif(
    PY_315_OR_ABOVE,
    reason="global PY_UNWIND clearing applies on 3.12–3.14",
)
def test_unregister_clears_global_unwind_pre_315() -> None:
    """Unregistering the last unwind handler clears global PY_UNWIND on 3.12–3.14."""

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


def test_mixed_local_events(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
) -> None:
    """A handler overriding both PY_START and PY_UNWIND gets each event enabled."""

    def fn() -> None:
        raise ValueError("mixed")

    handler: StartAndUnwindHandler = registered(fn.__code__, StartAndUnwindHandler())  # type: ignore[assignment]

    tool_id: int | None = monitoring._tool_id
    assert tool_id is not None

    local_events: int = _sys_monitoring.get_local_events(tool_id, fn.__code__)
    global_events: int = _sys_monitoring.get_events(tool_id)
    assert local_events & _E.PY_START, "PY_START must be a local event"
    if PY_315_OR_ABOVE:
        assert local_events & _E.PY_UNWIND, "PY_UNWIND must be a local event on 3.15+"
        assert not (global_events & _E.PY_UNWIND), "PY_UNWIND must not be global on 3.15+"
    else:
        assert not (local_events & _E.PY_UNWIND), "PY_UNWIND must not be local before 3.15"
        assert global_events & _E.PY_UNWIND, "PY_UNWIND must be global on 3.12–3.14"

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
