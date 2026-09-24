"""Tests for the multiplexed sys.monitoring layer on Python 3.12+."""

from collections.abc import Iterator
import gc
import sys
from types import CodeType
from typing import Any
from typing import Callable
from typing import Protocol
from typing import cast
import weakref

import pytest

from ddtrace.internal.compat import is_at_least_py


if not is_at_least_py(3, 12):
    pytest.skip("ddtrace.internal.monitoring requires Python 3.12+", allow_module_level=True)

from ddtrace.internal import monitoring


# PY_UNWIND became a per-code event only in 3.15; on 3.12-3.14 the multiplexer
# rejects handlers that need it.
_py315 = pytest.mark.skipif(not is_at_least_py(3, 15), reason="PY_UNWIND is per-code only on 3.15+")
_below_315 = pytest.mark.skipif(is_at_least_py(3, 15), reason="PY_UNWIND is global-only on 3.12-3.14")


class _MonitoringEvents(Protocol):
    """Subset of sys.monitoring.events used by these tests."""

    PY_START: int
    PY_RETURN: int
    PY_UNWIND: int
    LINE: int
    EXCEPTION_HANDLED: int


# `_E = sys.monitoring.events` has an indeterminate type when mypy analyzes the
# source module under a pre-3.15 Python version.
_E: _MonitoringEvents = cast(_MonitoringEvents, monitoring._E)
_DISABLE: object = cast(object, monitoring._DISABLE)
_LOCAL_EVENTS: int = cast(int, monitoring._LOCAL_EVENTS)
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


class StartHandler(monitoring.MonitoringEventHandler):
    def __init__(self) -> None:
        self.started: bool = False

    def on_py_start(self, code: CodeType, instruction_offset: int) -> object | None:
        self.started = True
        return None


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


class HandledExceptionHandler(monitoring.MonitoringEventHandler):
    def __init__(self) -> None:
        self.handled: list[tuple[CodeType, BaseException]] = []

    def on_exception_handled(self, code: CodeType, instruction_offset: int, exception: BaseException) -> None:
        self.handled.append((code, exception))


class RaisingHandledExceptionHandler(monitoring.MonitoringEventHandler):
    def on_exception_handled(self, code: CodeType, instruction_offset: int, exception: BaseException) -> None:
        raise RuntimeError("handled-exception handler exploded")


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


@pytest.fixture
def registered_global() -> Iterator[Callable[[monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler]]:
    registrations: list[monitoring.MonitoringEventHandler] = []

    def _register(handler: monitoring.MonitoringEventHandler) -> monitoring.MonitoringEventHandler:
        monitoring.register_global(handler)
        registrations.append(handler)
        return handler

    yield _register

    for handler in registrations:
        monitoring.unregister_global(handler)


@_py315
def test_register_unwind_handler_does_not_raise(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
) -> None:
    """Registering a PY_UNWIND-only handler enables the per-code unwind event."""

    def boom() -> None:
        raise ValueError("boom")

    registered(boom.__code__, UnwindHandler())


@_py315
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


@_py315
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


@_py315
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


@_py315
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


def test_line_disable_request_is_only_advisory_while_a_sibling_needs_events(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
) -> None:
    """A non-disabling sibling keeps real LINE callbacks active for every handler."""

    def fn() -> None:
        value = 1
        value += 1

    disabling: LineHandler = registered(fn.__code__, LineHandler(disable=True))  # type: ignore[assignment]
    passive: LineHandler = registered(fn.__code__, LineHandler())  # type: ignore[assignment]

    fn()
    first_disabling_lines = tuple(disabling.lines)
    first_passive_lines = tuple(passive.lines)
    assert first_disabling_lines
    assert first_passive_lines == first_disabling_lines

    fn()
    assert tuple(disabling.lines) == first_disabling_lines * 2
    assert tuple(passive.lines) == first_passive_lines * 2


def test_refresh_skips_event_that_was_not_physically_disabled(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected DISABLE request does not trigger an unnecessary event toggle."""

    def fn() -> None:
        value = 1
        value += 1

    registered(fn.__code__, LineHandler(disable=True))
    registered(fn.__code__, LineHandler())
    fn()

    def fail_rearm(*args: object) -> None:
        pytest.fail(f"unexpected physical re-arm: {args!r}")

    monkeypatch.setattr(monitoring, "_rearm_local_events", fail_rearm)
    monitoring.refresh(fn.__code__, _E.LINE)


def test_unrelated_handler_does_not_vote_on_line_disable(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
) -> None:
    """Only handlers interested in LINE participate in its DISABLE decision."""

    def fn() -> None:
        pass

    line_handler: LineHandler = registered(fn.__code__, LineHandler(disable=True))  # type: ignore[assignment]
    start_handler: StartHandler = registered(fn.__code__, StartHandler())  # type: ignore[assignment]

    result = monitoring._on_py_line(fn.__code__, fn.__code__.co_firstlineno)

    assert result is _DISABLE
    assert line_handler.lines
    assert not start_handler.started


def test_registering_unrelated_handler_does_not_rearm_disabled_line(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
) -> None:
    """Adding a different event kind leaves sticky LINE disables unchanged."""

    def fn() -> None:
        value = 1
        value += 1

    line_handler: LineHandler = registered(fn.__code__, LineHandler(disable=True))  # type: ignore[assignment]
    fn()
    disabled_lines = tuple(line_handler.lines)
    assert disabled_lines

    fn()
    assert tuple(line_handler.lines) == disabled_lines

    registered(fn.__code__, StartHandler())
    fn()
    assert tuple(line_handler.lines) == disabled_lines


def test_unregistering_last_line_non_disabler_allows_disable(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
) -> None:
    """The remaining handler can disable LINE on the first callback after unregister."""

    def fn() -> None:
        value = 1
        value += 1

    disabling: LineHandler = registered(fn.__code__, LineHandler(disable=True))  # type: ignore[assignment]
    passive: LineHandler = registered(fn.__code__, LineHandler())  # type: ignore[assignment]

    fn()
    first_lines = tuple(disabling.lines)
    assert first_lines
    fn()
    assert tuple(disabling.lines) == first_lines * 2

    monitoring.unregister(fn.__code__, passive)
    fn()
    assert tuple(disabling.lines) == first_lines * 3
    fn()
    assert tuple(disabling.lines) == first_lines * 3


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


@_py315
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
    sibling: StartHandler = registered(fn.__code__, StartHandler())  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="start handler exploded"):
        monitoring._on_py_start(fn.__code__, 0)

    assert raiser.called
    assert not sibling.started, "a sibling handler after a propagating raiser must not run"


def test_multiplexer_uses_only_tool_id_3() -> None:
    """Slot 4 remains available until the exception profiler joins the multiplexer."""
    assert monitoring._CANDIDATE_TOOL_IDS == (3,)
    assert monitoring.get_tool_id() == 3


@pytest.mark.subprocess(out=None, err=None)
def test_last_collected_code_releases_tool_and_callbacks() -> None:
    import gc
    import sys
    from types import CodeType
    import weakref

    from ddtrace.internal import monitoring

    sys_monitoring = getattr(sys, "monitoring")

    class Handler(monitoring.MonitoringEventHandler):
        def on_py_line(self, code: CodeType, line_number: int) -> None:
            pass

    code = compile("pass", "<collected>", "exec")
    collected = weakref.ref(code)
    monitoring.register(code, Handler())
    tool_id = monitoring._tool_id
    assert tool_id == 3

    del code
    gc.collect()

    assert collected() is None
    assert monitoring._tool_id is None
    assert sys_monitoring.get_tool(tool_id) is None
    assert sys_monitoring.get_events(tool_id) == 0


@pytest.mark.subprocess(out=None, err=None)
def test_last_unregister_releases_tool_and_callbacks() -> None:
    import sys
    from types import CodeType

    from ddtrace.internal import monitoring

    sys_monitoring = getattr(sys, "monitoring")

    class Handler(monitoring.MonitoringEventHandler):
        def on_py_line(self, code: CodeType, line_number: int) -> None:
            pass

    def target() -> None:
        pass

    handler = Handler()
    monitoring.register(target.__code__, handler)
    tool_id = monitoring._tool_id
    assert tool_id == 3

    monitoring.unregister(target.__code__, handler)

    assert monitoring._tool_id is None
    assert sys_monitoring.get_tool(tool_id) is None
    assert sys_monitoring.get_events(tool_id) == 0

    sys_monitoring.use_tool_id(tool_id, "external")

    def callback(code: CodeType, line_number: int) -> None:
        pass

    assert sys_monitoring.register_callback(tool_id, sys_monitoring.events.LINE, callback) is None
    sys_monitoring.register_callback(tool_id, sys_monitoring.events.LINE, None)
    sys_monitoring.free_tool_id(tool_id)

    monitoring.register(target.__code__, handler)
    assert monitoring._tool_id == tool_id
    monitoring.unregister(target.__code__, handler)


def test_registration_holds_registry_lock_while_claiming_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """Final teardown cannot free the tool between setup and registry publication."""
    import threading

    class Handler(monitoring.MonitoringEventHandler):
        def on_py_line(self, code: CodeType, line_number: int) -> None:
            pass

    first_code = compile("pass", "<first>", "exec")
    second_code = compile("pass", "<second>", "exec")
    handler = Handler()
    monitoring.register(first_code, handler)

    original_setup = monitoring._setup
    setup_returned = threading.Event()
    allow_registration = threading.Event()
    unregister_finished = threading.Event()
    errors: list[BaseException] = []

    def blocked_setup() -> int:
        tool_id = original_setup()
        setup_returned.set()
        allow_registration.wait(timeout=5)
        return tool_id

    def register_second() -> None:
        try:
            monitoring.register(second_code, handler)
        except BaseException as error:
            errors.append(error)

    def unregister_first() -> None:
        try:
            monitoring.unregister(first_code, handler)
        except BaseException as error:
            errors.append(error)
        finally:
            unregister_finished.set()

    monkeypatch.setattr(monitoring, "_setup", blocked_setup)
    register_thread = threading.Thread(target=register_second)
    register_thread.start()
    assert setup_returned.wait(timeout=5)

    unregister_thread = threading.Thread(target=unregister_first)
    unregister_thread.start()
    teardown_won_race = unregister_finished.wait(timeout=0.1)
    allow_registration.set()
    register_thread.join(timeout=5)
    unregister_thread.join(timeout=5)

    assert not teardown_won_race
    assert not register_thread.is_alive()
    assert not unregister_thread.is_alive()
    assert not errors
    assert monitoring._registry.get(second_code) is not None
    assert monitoring._tool_id is not None

    monitoring.unregister(second_code, handler)


@pytest.mark.subprocess(out=None, err=None)
def test_weak_cleanup_can_reenter_registry_lock_during_gc() -> None:
    import gc
    import threading
    from types import CodeType

    from ddtrace.internal import monitoring

    class Handler(monitoring.MonitoringEventHandler):
        def on_py_line(self, code: CodeType, line_number: int) -> None:
            pass

    gc.disable()
    first_code = compile("pass", "<cyclic-first>", "exec")
    first_handler = Handler()
    monitoring.register(first_code, first_handler)

    cycle: list[object] = [first_code]
    cycle.append(cycle)
    del first_code
    del cycle

    second_code = compile("pass", "<cyclic-second>", "exec")
    second_handler = Handler()
    original_set_local_events = monitoring._set_local_events
    errors: list[BaseException] = []

    def collect_during_registration(tool_id: int, code: CodeType, events: int) -> None:
        gc.collect()
        original_set_local_events(tool_id, code, events)

    def register_second() -> None:
        try:
            monitoring.register(second_code, second_handler)
        except BaseException as error:
            errors.append(error)

    monitoring._set_local_events = collect_during_registration
    thread = threading.Thread(target=register_second, daemon=True)
    thread.start()
    thread.join(timeout=5)

    assert not thread.is_alive(), "weak cleanup deadlocked while re-entering the registry lock"
    assert not errors

    monitoring._set_local_events = original_set_local_events
    monitoring.unregister(second_code, second_handler)
    gc.enable()


@pytest.mark.subprocess(out=None, err=None)
def test_get_tool_id_does_not_fall_back_to_profiler_slot() -> None:
    """An occupied slot 3 is preserved without claiming the profiler's slot 4."""
    import sys

    import pytest

    sys_monitoring = getattr(sys, "monitoring")
    sys_monitoring.use_tool_id(3, "external")

    from ddtrace.internal import monitoring

    with pytest.raises(monitoring.MonitoringToolUnavailable):
        monitoring.get_tool_id()

    assert sys_monitoring.get_tool(3) == "external"
    assert sys_monitoring.get_tool(4) is None


@pytest.mark.subprocess(out=None, err=None)
def test_get_tool_id_fails_without_disturbing_occupied_slots() -> None:
    """Tool setup preserves both custom-slot owners when slot 3 is unavailable."""
    import sys

    import pytest

    sys_monitoring = getattr(sys, "monitoring")
    sys_monitoring.use_tool_id(3, "external-3")
    sys_monitoring.use_tool_id(4, "external-4")

    from ddtrace.internal import monitoring

    with pytest.raises(monitoring.MonitoringToolUnavailable):
        monitoring.get_tool_id()

    assert sys_monitoring.get_tool(3) == "external-3"
    assert sys_monitoring.get_tool(4) == "external-4"


def test_py_start_disable_forwarded_when_all_handlers_return_disable(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
) -> None:
    """DISABLE is forwarded to CPython when every PY_START handler for the code returns it."""

    class DisablingStartHandler(monitoring.MonitoringEventHandler):
        def __init__(self) -> None:
            self.count: int = 0

        def on_py_start(self, code: CodeType, instruction_offset: int) -> object | None:
            self.count += 1
            return _DISABLE

    def fn() -> None:
        pass

    handler: DisablingStartHandler = registered(fn.__code__, DisablingStartHandler())  # type: ignore[assignment]

    # The DISABLE return silences further PY_START events until refresh(): the two real calls
    # below must only fire once.
    fn()
    fn()
    assert handler.count == 1, "PY_START must not fire again after DISABLE"

    monitoring.refresh(fn.__code__, _E.PY_START)
    fn()
    assert handler.count == 2, "refresh() must re-arm the disabled PY_START event"


def test_refresh_rearms_only_requested_event_kind(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
) -> None:
    """refresh() re-arms requested events without disturbing unrelated disables."""

    class DisablingStartAndLineHandler(monitoring.MonitoringEventHandler):
        def __init__(self) -> None:
            self.starts = 0
            self.lines: list[int] = []

        def on_py_start(self, code: CodeType, instruction_offset: int) -> object | None:
            self.starts += 1
            return _DISABLE

        def on_py_line(self, code: CodeType, line_number: int) -> object | None:
            self.lines.append(line_number)
            return _DISABLE

    def fn() -> None:
        value = 1
        value += 1

    handler: DisablingStartAndLineHandler = registered(  # type: ignore[assignment]
        fn.__code__, DisablingStartAndLineHandler()
    )

    fn()
    first_lines = tuple(handler.lines)
    assert handler.starts == 1
    assert first_lines

    fn()
    assert handler.starts == 1
    assert tuple(handler.lines) == first_lines

    monitoring.refresh(fn.__code__, _E.LINE)
    fn()
    assert handler.starts == 1
    assert tuple(handler.lines) == first_lines * 2

    monitoring.refresh(fn.__code__, _E.PY_START)
    fn()
    assert handler.starts == 2
    assert tuple(handler.lines) == first_lines * 2


def test_register_rearms_disabled_py_start_for_new_handler(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
) -> None:
    """Adding a handler re-arms an event disabled by an existing handler."""

    class DisablingStartHandler(monitoring.MonitoringEventHandler):
        def __init__(self) -> None:
            self.count = 0

        def on_py_start(self, code: CodeType, instruction_offset: int) -> object | None:
            self.count += 1
            return _DISABLE

    class PassiveStartHandler(monitoring.MonitoringEventHandler):
        def __init__(self) -> None:
            self.count = 0

        def on_py_start(self, code: CodeType, instruction_offset: int) -> None:
            self.count += 1

    def fn() -> None:
        pass

    disabling: DisablingStartHandler = registered(fn.__code__, DisablingStartHandler())  # type: ignore[assignment]
    fn()
    fn()
    assert disabling.count == 1

    passive: PassiveStartHandler = registered(fn.__code__, PassiveStartHandler())  # type: ignore[assignment]
    fn()
    fn()

    assert disabling.count == 3
    assert passive.count == 2


def test_py_start_continues_when_any_handler_declines_disable(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
) -> None:
    """PY_START events continue if any registered handler returns something other than DISABLE."""

    class DisablingStartHandler(monitoring.MonitoringEventHandler):
        def on_py_start(self, code: CodeType, instruction_offset: int) -> object | None:
            return _DISABLE

    class PassiveStartHandler(monitoring.MonitoringEventHandler):
        def __init__(self) -> None:
            self.count: int = 0

        def on_py_start(self, code: CodeType, instruction_offset: int) -> object | None:
            self.count += 1
            return None

    def fn() -> None:
        pass

    passive: PassiveStartHandler = registered(fn.__code__, PassiveStartHandler())  # type: ignore[assignment]
    registered(fn.__code__, DisablingStartHandler())

    result: object | None = monitoring._on_py_start(fn.__code__, 0)
    assert result is not _DISABLE, "a None-returning handler must keep PY_START events firing"
    assert passive.count == 1


@_py315
def test_unregistering_unwind_handler_preserves_unrelated_start_event(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
) -> None:
    """Removing the last UNWIND handler leaves the code object's START event enabled."""

    def fn() -> None:
        raise ValueError("boom")

    start_handler: StartHandler = registered(fn.__code__, StartHandler())  # type: ignore[assignment]
    unwind_handler: UnwindHandler = registered(fn.__code__, UnwindHandler())  # type: ignore[assignment]
    tool_id = monitoring._tool_id
    assert tool_id is not None

    monitoring.unregister(fn.__code__, unwind_handler)
    local_events = _sys_monitoring.get_local_events(tool_id, fn.__code__)
    assert local_events & _E.PY_START
    assert not local_events & _E.PY_UNWIND

    with pytest.raises(ValueError, match="boom"):
        fn()

    assert start_handler.started
    assert not unwind_handler.unwinds


@_below_315
def test_py_unwind_handler_rejected_below_315() -> None:
    """A handler overriding on_py_unwind cannot be registered on 3.12-3.14."""

    class UnwindOnly(monitoring.MonitoringEventHandler):
        def on_py_unwind(self, code: CodeType, instruction_offset: int, exception: BaseException) -> None:
            pass

    def fn() -> None:
        pass

    with pytest.raises(RuntimeError, match="on_py_unwind handlers require Python 3.15+"):
        monitoring.register(fn.__code__, UnwindOnly())


@_below_315
def test_mixed_py_unwind_handler_rejection_is_atomic() -> None:
    """Rejecting a mixed handler does not leave its supported events registered."""

    def fn() -> None:
        pass

    with pytest.raises(RuntimeError, match="on_py_unwind handlers require Python 3.15+"):
        monitoring.register(fn.__code__, StartAndUnwindHandler())

    assert monitoring._registry.get(fn.__code__) is None


@_below_315
def test_local_events_exclude_py_unwind_below_315() -> None:
    """_LOCAL_EVENTS must omit PY_UNWIND when set_local_events rejects it."""

    assert not (_LOCAL_EVENTS & _E.PY_UNWIND), "PY_UNWIND is not a local event on 3.12-3.14"
    assert _LOCAL_EVENTS & _E.PY_START
    assert _LOCAL_EVENTS & _E.PY_RETURN
    assert _LOCAL_EVENTS & _E.LINE


def test_global_and_local_events_share_the_same_tool(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
    registered_global: Callable[[monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
) -> None:
    """A global EXCEPTION_HANDLED handler coexists with a local LINE handler."""

    def fn() -> None:
        try:
            raise ValueError("handled")
        except ValueError:
            return

    line_handler: LineHandler = registered(fn.__code__, LineHandler())  # type: ignore[assignment]
    exception_handler = cast(HandledExceptionHandler, registered_global(HandledExceptionHandler()))

    tool_id = monitoring._tool_id
    assert tool_id is not None
    assert _sys_monitoring.get_events(tool_id) & _E.EXCEPTION_HANDLED
    assert _sys_monitoring.get_local_events(tool_id, fn.__code__) & _E.LINE

    fn()

    assert line_handler.lines
    assert any(code is fn.__code__ and exc.args == ("handled",) for code, exc in exception_handler.handled)

    handled_count = len(exception_handler.handled)
    monitoring.unregister_global(exception_handler)
    assert not (_sys_monitoring.get_events(tool_id) & _E.EXCEPTION_HANDLED)
    assert _sys_monitoring.get_local_events(tool_id, fn.__code__) & _E.LINE

    fn()
    assert len(exception_handler.handled) == handled_count


def test_global_registration_preserves_disabled_local_events(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
    registered_global: Callable[[monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
) -> None:
    """Changing global events must not reset local DISABLE state."""

    def fn() -> None:
        value = 1
        value += 1

    line_handler: LineHandler = registered(fn.__code__, LineHandler(disable=True))  # type: ignore[assignment]
    fn()
    disabled_count = len(line_handler.lines)
    fn()
    assert len(line_handler.lines) == disabled_count

    exception_handler = registered_global(HandledExceptionHandler())
    fn()
    assert len(line_handler.lines) == disabled_count

    monitoring.refresh(fn.__code__, _E.LINE)
    fn()
    refreshed_count = len(line_handler.lines)
    assert refreshed_count > disabled_count

    monitoring.unregister_global(exception_handler)
    fn()
    assert len(line_handler.lines) == refreshed_count


def test_exception_handled_handler_failure_does_not_affect_user_code(
    registered_global: Callable[[monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
) -> None:
    """A failing global handler is isolated from user code."""

    def fn() -> None:
        pass

    registered_global(RaisingHandledExceptionHandler())
    monitoring._on_exception_handled(fn.__code__, 0, ValueError("handled"))


def test_register_global_rejects_different_exception_handler(
    registered_global: Callable[[monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
) -> None:
    """A second owner cannot silently replace the active global handler."""

    def fn() -> None:
        pass

    first: HandledExceptionHandler = registered_global(HandledExceptionHandler())  # type: ignore[assignment]
    second = HandledExceptionHandler()

    with pytest.raises(ValueError, match="already has a different"):
        monitoring.register_global(second)

    first.handled.clear()
    exception = ValueError("handled")
    monitoring._on_exception_handled(fn.__code__, 0, exception)
    assert (fn.__code__, exception) in first.handled
    assert not second.handled

    monitoring.unregister_global(second)
    first.handled.clear()
    monitoring._on_exception_handled(fn.__code__, 0, exception)
    assert (fn.__code__, exception) in first.handled


def test_register_global_rejects_local_only_handler() -> None:
    with pytest.raises(ValueError, match="no global"):
        monitoring.register_global(LineHandler())


@pytest.mark.parametrize("direct", [False, True])
def test_global_registration_keeps_original_delivery_mode(direct: bool) -> None:
    def target() -> None:
        try:
            raise ValueError("handled")
        except ValueError:
            pass

    handler = HandledExceptionHandler()
    monitoring.register_global(handler, direct=direct)
    try:
        monitoring.register_global(handler, direct=not direct)
        with pytest.raises(ValueError, match="already has a different"):
            monitoring.register_global(HandledExceptionHandler(), direct=True)

        tool_id = monitoring.get_tool_id()
        expected = handler.on_exception_handled if direct else monitoring._on_exception_handled
        assert _sys_monitoring.register_callback(tool_id, _E.EXCEPTION_HANDLED, expected) == expected

        target()
        assert any(code is target.__code__ and exc.args == ("handled",) for code, exc in handler.handled)
    finally:
        monitoring.unregister_global(handler)


@pytest.mark.parametrize("failure", ["callback", "events"])
def test_direct_global_install_rolls_back_callback(
    monkeypatch: pytest.MonkeyPatch,
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
    failure: str,
) -> None:
    def target() -> None:
        pass

    local = cast(LineHandler, registered(target.__code__, LineHandler()))
    tool_id = monitoring.get_tool_id()
    handler = HandledExceptionHandler()
    register_callback = _sys_monitoring.register_callback
    set_events = _sys_monitoring.set_events

    def failing_register_callback(tool: int, event: int, callback: Any) -> Any:
        if event == _E.EXCEPTION_HANDLED and callback == handler.on_exception_handled:
            raise RuntimeError("callback install failed")
        return register_callback(tool, event, callback)

    def failing_set_events(tool: int, events: int) -> None:
        if events:
            raise RuntimeError("event install failed")
        set_events(tool, events)

    with monkeypatch.context() as patch:
        if failure == "callback":
            patch.setattr(_sys_monitoring, "register_callback", failing_register_callback)
        else:
            patch.setattr(_sys_monitoring, "set_events", failing_set_events)
        with pytest.raises(RuntimeError, match="install failed"):
            monitoring.register_global(handler, direct=True)

    assert monitoring._global_exception_handler is None
    assert _sys_monitoring.get_events(tool_id) == 0
    assert (
        register_callback(tool_id, _E.EXCEPTION_HANDLED, monitoring._on_exception_handled)
        is monitoring._on_exception_handled
    )
    target()
    assert local.lines


def test_direct_global_unregister_releases_callback(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
) -> None:
    def target() -> None:
        pass

    local = registered(target.__code__, LineHandler())
    tool_id = monitoring.get_tool_id()
    handler = HandledExceptionHandler()
    handler_ref = weakref.ref(handler)
    monitoring.register_global(handler, direct=True)
    monitoring.unregister_global(handler)
    del handler
    gc.collect()

    assert handler_ref() is None
    assert not (_sys_monitoring.get_events(tool_id) & _E.EXCEPTION_HANDLED)
    assert (
        _sys_monitoring.register_callback(tool_id, _E.EXCEPTION_HANDLED, monitoring._on_exception_handled)
        is monitoring._on_exception_handled
    )
    monitoring.unregister(target.__code__, local)
    assert monitoring._tool_id is None
