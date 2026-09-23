"""Tests for the multiplexed sys.monitoring layer on Python 3.12+."""

from collections.abc import Iterator
import sys
import threading
from types import CodeType
from typing import Any
from typing import Callable
from typing import Protocol
from typing import cast

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


@pytest.mark.subprocess(out=None, err=None)
def test_global_restart_requires_sole_requester_and_no_external_tool() -> None:
    """The best-effort global shortcut rejects non-owners, visible tools, and siblings."""
    import sys
    from types import CodeType

    from ddtrace.internal import monitoring

    sys_monitoring = getattr(sys, "monitoring")

    class Handler(monitoring.MonitoringEventHandler):
        def on_py_start(self, code: CodeType, instruction_offset: int) -> None:
            pass

    first_code = compile("pass", "<first>", "exec")
    first = Handler()
    monitoring.register(first_code, first)

    outsider = Handler()
    assert monitoring.restart_events(outsider) is None

    version = monitoring.restart_events(first)
    assert version is not None
    assert monitoring.subscriber_version_is_current(version)

    # Registering more code for the same subscriber must keep the ownership version valid.
    # Coverage instruments many code objects between contexts; invalidating here would make
    # each restart rescan the entire registry and turn that workload quadratic.
    same_subscriber_code = compile("pass", "<same-subscriber>", "exec")
    monitoring.register(same_subscriber_code, first)
    assert monitoring.subscriber_version_is_current(version)
    assert monitoring.restart_events(first) == version

    own_tool = monitoring.get_tool_id()
    external_tool = next(
        tool_id for tool_id in range(6) if tool_id != own_tool and sys_monitoring.get_tool(tool_id) is None
    )
    sys_monitoring.use_tool_id(external_tool, "external")
    try:
        assert monitoring.restart_events(first) is None
    finally:
        sys_monitoring.free_tool_id(external_tool)

    second_code = compile("pass", "<second>", "exec")
    second = Handler()
    monitoring.register(second_code, second)
    assert monitoring.restart_events(first) is None
    assert monitoring.restart_events(second) is None
    assert not monitoring.subscriber_version_is_current(version)


@pytest.mark.subprocess(out=None, err=None)
def test_subscriber_version_tracks_distinct_subscribers() -> None:
    """The version changes only when the set of distinct subscribers changes."""
    from types import CodeType

    from ddtrace.internal import monitoring

    class Handler(monitoring.MonitoringEventHandler):
        def on_py_start(self, code: CodeType, instruction_offset: int) -> None:
            pass

    sole = Handler()
    codes = [compile("pass", f"<code{index}>", "exec") for index in range(3)]

    monitoring.register(codes[0], sole)
    version = monitoring.restart_events(sole)
    assert version is not None

    for code in codes[1:]:
        monitoring.register(code, sole)
    monitoring.register(codes[0], sole)
    assert monitoring.subscriber_version_is_current(version)
    assert monitoring.restart_events(sole) == version

    for code in codes[1:]:
        monitoring.unregister(code, sole)
    assert monitoring.subscriber_version_is_current(version)

    monitoring.unregister(codes[0], sole)
    assert not monitoring.subscriber_version_is_current(version)


@pytest.mark.subprocess(out=None, err=None)
def test_sole_subscriber_survives_collected_code_objects() -> None:
    """Collected code must not permanently hide the remaining sole subscriber."""
    import gc
    from types import CodeType
    import weakref

    from ddtrace.internal import monitoring

    class Handler(monitoring.MonitoringEventHandler):
        def on_py_start(self, code: CodeType, instruction_offset: int) -> None:
            pass

    sole = Handler()
    sole_code = compile("pass", "<sole>", "exec")
    monitoring.register(sole_code, sole)

    ghost = Handler()
    ghost_code = compile("pass", "<ghost>", "exec")
    monitoring.register(ghost_code, ghost)
    assert monitoring.restart_events(sole) is None

    collected = weakref.ref(ghost_code)
    del ghost_code
    gc.collect()
    assert collected() is None

    version = monitoring.restart_events(sole)
    assert version is not None
    assert monitoring.restart_events(sole) == version
    assert ghost is not None


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


def test_concurrent_event_callbacks_retain_each_disabled_event(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
) -> None:
    """Concurrent event kinds cannot overwrite each other's disabled-event state."""

    class Handler(monitoring.MonitoringEventHandler):
        def __init__(self) -> None:
            self.barrier = threading.Barrier(2)

        def on_py_start(self, code: CodeType, instruction_offset: int) -> object:
            self.barrier.wait()
            return _DISABLE

        def on_py_line(self, code: CodeType, line_number: int) -> object:
            self.barrier.wait()
            return _DISABLE

    def fn() -> None:
        pass

    registered(fn.__code__, Handler())
    results: list[object | None] = []
    start_thread = threading.Thread(target=lambda: results.append(monitoring._on_py_start(fn.__code__, 0)))
    line_thread = threading.Thread(
        target=lambda: results.append(monitoring._on_py_line(fn.__code__, fn.__code__.co_firstlineno))
    )

    start_thread.start()
    line_thread.start()
    start_thread.join()
    line_thread.join()

    handlers = monitoring._registry.get(fn.__code__)
    assert handlers is not None
    assert monitoring._possibly_disabled_events(handlers) == _E.PY_START | _E.LINE
    assert results == [_DISABLE, _DISABLE]


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
    """Coverage uses only slot 3 until handled-exception ownership is finalized."""
    assert monitoring._CANDIDATE_TOOL_IDS == (3,)
    assert monitoring.get_tool_id() == 3


@pytest.mark.subprocess(timeout=10, out=None, err=None)
def test_batch_registration_materializes_one_shot_iterable_before_locking() -> None:
    from types import CodeType

    from ddtrace.internal import monitoring

    class Handler(monitoring.MonitoringEventHandler):
        def on_py_line(self, code: CodeType, line_number: int) -> None:
            pass

    def codes():
        yield compile("pass", "<first>", "exec")
        yield compile("pass", "<second>", "exec")

    monitoring._register_many(codes(), Handler(), events=monitoring._E.LINE)

    # The batch tuple is released after the registry lock. Both code objects can
    # then be collected and weakref cleanup releases the now-unused tool.
    assert monitoring._tool_id is None


@pytest.mark.subprocess(out=None, err=None)
def test_tool_reservation_releases_unregistered_slot() -> None:
    import sys

    from ddtrace.internal import monitoring

    sys_monitoring = getattr(sys, "monitoring")
    with monitoring._reserve_tool_id() as tool_id:
        assert tool_id == 3
        assert sys_monitoring.get_tool(tool_id) == "ddtrace"

    assert monitoring._tool_id is None
    assert sys_monitoring.get_tool(tool_id) is None


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


@pytest.mark.subprocess(out=None, err=None)
def test_get_tool_id_ignores_occupied_non_candidate_slot() -> None:
    """An owner of non-candidate slot 4 does not affect multiplexer setup on slot 3."""
    import sys

    sys_monitoring = getattr(sys, "monitoring")
    sys_monitoring.use_tool_id(4, "external")

    from ddtrace.internal import monitoring

    assert monitoring.get_tool_id() == 3
    assert sys_monitoring.get_tool(4) == "external"
    assert sys_monitoring.get_tool(3) == "ddtrace"


@pytest.mark.subprocess(out=None, err=None)
def test_get_tool_id_fails_without_falling_back_to_slot_4() -> None:
    """Tool setup preserves slot 3's owner and does not fall back to slot 4."""
    import sys

    import pytest

    sys_monitoring = getattr(sys, "monitoring")
    sys_monitoring.use_tool_id(3, "external")

    from ddtrace.internal import monitoring

    with pytest.raises(monitoring.MonitoringToolUnavailable):
        monitoring.get_tool_id()

    assert sys_monitoring.get_tool(3) == "external"
    assert sys_monitoring.get_tool(4) is None


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


@pytest.mark.parametrize("callback_name", ["_on_py_start", "_on_py_line"])
def test_register_invalidates_inflight_disable(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
    callback_name: str,
) -> None:
    """A handler registered during dispatch is not hidden by that dispatch's DISABLE."""
    started = threading.Event()
    release = threading.Event()
    results: list[object | None] = []

    class BlockingHandler(monitoring.MonitoringEventHandler):
        def __init__(self) -> None:
            self.count = 0

        def on_py_start(self, code: CodeType, instruction_offset: int) -> object | None:
            self.count += 1
            started.set()
            release.wait()
            return _DISABLE

        def on_py_line(self, code: CodeType, line_number: int) -> object | None:
            self.count += 1
            started.set()
            release.wait()
            return _DISABLE

    def fn() -> None:
        pass

    first = registered(fn.__code__, BlockingHandler())
    second = BlockingHandler()
    callback = getattr(monitoring, callback_name)

    thread = threading.Thread(target=lambda: results.append(callback(fn.__code__, 1)))
    thread.start()
    try:
        assert started.wait(timeout=5)
        registered(fn.__code__, second)
    finally:
        release.set()
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert results == [None]
    assert second.count == 0
    assert callback(fn.__code__, 1) is _DISABLE
    assert second.count == 1
    monitoring.unregister(fn.__code__, first)
    assert callback(fn.__code__, 1) is _DISABLE
    assert second.count == 2


@pytest.mark.parametrize("callback_name", ["_on_py_start", "_on_py_line"])
def test_callback_snapshots_follow_registration_order(
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
    callback_name: str,
) -> None:
    calls: list[tuple[str, str]] = []

    class Handler(monitoring.MonitoringEventHandler):
        def __init__(self, name: str) -> None:
            self.name = name

        def on_py_start(self, code: CodeType, instruction_offset: int) -> object:
            calls.append((self.name, "_on_py_start"))
            return _DISABLE

        def on_py_line(self, code: CodeType, line_number: int) -> object:
            calls.append((self.name, "_on_py_line"))
            return _DISABLE

    def fn() -> None:
        pass

    first = registered(fn.__code__, Handler("first"))
    second = registered(fn.__code__, Handler("second"))
    callback = getattr(monitoring, callback_name)
    assert callback(fn.__code__, 1) is _DISABLE
    assert calls == [("first", callback_name), ("second", callback_name)]

    calls.clear()
    registered(fn.__code__, first)
    assert callback(fn.__code__, 1) is _DISABLE
    assert calls == [("first", callback_name), ("second", callback_name)]

    calls.clear()
    monitoring.unregister(fn.__code__, first)
    registered(fn.__code__, first)
    assert callback(fn.__code__, 1) is _DISABLE
    assert calls == [("second", callback_name), ("first", callback_name)]

    calls.clear()
    monitoring.unregister(fn.__code__, second)
    assert callback(fn.__code__, 1) is _DISABLE
    assert calls == [("first", callback_name)]


@pytest.mark.parametrize(("callback_name", "event"), [("_on_py_start", _E.PY_START), ("_on_py_line", _E.LINE)])
def test_refresh_after_disable_publication_observes_pending_vote(
    monkeypatch: pytest.MonkeyPatch,
    registered: Callable[[CodeType, monitoring.MonitoringEventHandler], monitoring.MonitoringEventHandler],
    callback_name: str,
    event: int,
) -> None:
    """A refresh between DISABLE publication and validation observes the pending vote."""

    class DisablingHandler(monitoring.MonitoringEventHandler):
        def on_py_start(self, code: CodeType, instruction_offset: int) -> object:
            return _DISABLE

        def on_py_line(self, code: CodeType, line_number: int) -> object:
            return _DISABLE

    def fn() -> None:
        pass

    registered(fn.__code__, DisablingHandler())
    rearmed: list[int] = []
    monkeypatch.setattr(
        monitoring,
        "_rearm_local_events",
        lambda _tool_id, _code, _events, rearm_events: rearmed.append(rearm_events),
    )

    class RefreshingEpoch(int):
        refreshed = False

        def __eq__(self, other: object) -> bool:
            if not self.refreshed:
                self.refreshed = True
                monitoring.refresh(fn.__code__, event)
            return False

    monkeypatch.setattr(monitoring, "_event_mutation_epoch", RefreshingEpoch(monitoring._event_mutation_epoch))

    callback = getattr(monitoring, callback_name)
    assert callback(fn.__code__, 1) is None
    assert rearmed == [event]


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
