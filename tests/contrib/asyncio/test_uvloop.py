import asyncio
import contextvars
from importlib.util import find_spec
import socket
import sys

import pytest

from ddtrace.contrib.internal.asyncio.patch import patch
from ddtrace.contrib.internal.asyncio.patch import unpatch
from ddtrace.internal import core
from ddtrace.trace import Span


_PATCHED_METHODS = {
    "create_task",
    "call_soon",
    "call_soon_threadsafe",
    "call_later",
    "call_at",
    "add_reader",
    "add_writer",
    "add_signal_handler",
    "run_forever",
}
_CONTEXT_WATCHER_AVAILABLE = sys.implementation.name == "cpython" and sys.version_info >= (3, 14)
_requires_context_switch_instrumentation = pytest.mark.skipif(
    _CONTEXT_WATCHER_AVAILABLE, reason="CPython 3.14+ uses the native context watcher"
)


@pytest.fixture
def uvloop_module():
    return pytest.importorskip("uvloop")


@pytest.fixture
def patched_uvloop(uvloop_module):
    # asyncio may already be patched, in which case patch() would be a no-op and
    # uvloop would not be patched at all.
    unpatch()
    patch()
    yield uvloop_module
    unpatch()


def _captured_context(tracer, active):
    tracer.context_provider.activate(active)
    context = contextvars.copy_context()
    tracer.context_provider.activate(None)
    return context


@pytest.mark.skipif(find_spec("uvloop") is None, reason="uvloop is not installed")
@pytest.mark.subprocess()
def test_uvloop_patched_on_import():
    import sys

    from ddtrace.contrib.internal.asyncio.patch import patch
    from ddtrace.contrib.internal.asyncio.patch import unpatch

    context_watcher_available = sys.implementation.name == "cpython" and sys.version_info >= (3, 14)
    patch()

    import uvloop

    assert hasattr(uvloop.Loop.call_soon, "__wrapped__") is not context_watcher_available
    unpatch()
    assert not hasattr(uvloop.Loop.call_soon, "__wrapped__")


def test_uvloop_unpatch_restores_inherited_methods(uvloop_module):
    # asyncio may already be patched, in which case uvloop is patched too.
    unpatch()
    original_attributes = {name: uvloop_module.Loop.__dict__.get(name) for name in _PATCHED_METHODS}

    patch()
    try:
        assert (_PATCHED_METHODS <= uvloop_module.Loop.__dict__.keys()) is not _CONTEXT_WATCHER_AVAILABLE
    finally:
        unpatch()

    assert {name: uvloop_module.Loop.__dict__.get(name) for name in _PATCHED_METHODS} == original_attributes


@_requires_context_switch_instrumentation
def test_uvloop_callback_restores_loop_context_for_late_listener(tracer, patched_uvloop):
    """A listener registered while the loop runs still sees the loop Context restored."""
    loop = patched_uvloop.new_event_loop()
    span = Span("callback")
    caller = Span("caller")
    context = _captured_context(tracer, span)
    switches = []
    observed = []

    def record_context_switch():
        switches.append(tracer.context_provider.active())

    def stop():
        observed.append(len(switches) - 1)
        loop.stop()

    def register_listener():
        core.on("python.context.switch", record_context_switch)
        loop.call_soon(stop, context=context)

    try:
        loop.call_soon(register_listener)
        tracer.context_provider.activate(caller)
        loop.run_forever()
    finally:
        core.reset_listeners("python.context.switch", record_context_switch)
        loop.close()
        span.finish()
        caller.finish()
        tracer.context_provider.activate(None)

    assert len(observed) == 1
    event_index = observed[0]
    assert switches[event_index : event_index + 2] == [span, caller]


@_requires_context_switch_instrumentation
def test_uvloop_task_events_follow_each_resumption(tracer, patched_uvloop):
    loop = patched_uvloop.new_event_loop()
    first = Span("first")
    second = Span("second")
    caller = Span("caller")
    first_context = _captured_context(tracer, first)
    second_context = _captured_context(tracer, second)
    switches = []
    observed = []

    def record_context_switch():
        switches.append(tracer.context_provider.active())

    async def worker(expected):
        for _ in range(3):
            observed.append((len(switches) - 1, expected))
            await asyncio.sleep(0)

    core.on("python.context.switch", record_context_switch)
    try:
        tasks = (
            first_context.run(loop.create_task, worker(first)),
            second_context.run(loop.create_task, worker(second)),
        )
        tracer.context_provider.activate(caller)
        loop.run_until_complete(asyncio.gather(*tasks))
    finally:
        core.reset_listeners("python.context.switch", record_context_switch)
        loop.close()
        first.finish()
        second.finish()
        caller.finish()
        tracer.context_provider.activate(None)

    # Each task step publishes its own context, then the context the loop was
    # started in.
    for event_index, expected in observed:
        assert switches[event_index : event_index + 2] == [expected, caller]


@_requires_context_switch_instrumentation
def test_uvloop_callback_events_follow_captured_context(tracer, patched_uvloop):
    loop = patched_uvloop.new_event_loop()
    read_socket, write_socket = socket.socketpair()
    span = Span("callback")
    caller = Span("caller")
    context = _captured_context(tracer, span)
    switches = []
    observed = []

    def record_context_switch():
        switches.append(tracer.context_provider.active())

    def callback(name):
        observed.append((name, len(switches) - 1))
        if name == "reader":
            loop.remove_reader(read_socket.fileno())
        if len(observed) == 2:
            loop.stop()

    core.on("python.context.switch", record_context_switch)
    try:
        loop.call_soon(callback, "soon", context=context)
        context.run(loop.add_reader, read_socket.fileno(), callback, "reader")
        write_socket.send(b"x")
        tracer.context_provider.activate(caller)
        loop.run_forever()
    finally:
        core.reset_listeners("python.context.switch", record_context_switch)
        read_socket.close()
        write_socket.close()
        loop.close()
        span.finish()
        caller.finish()
        tracer.context_provider.activate(None)

    assert {name for name, _ in observed} == {"soon", "reader"}
    for _, event_index in observed:
        assert switches[event_index : event_index + 2] == [span, caller]


@_requires_context_switch_instrumentation
def test_uvloop_callback_passed_as_keyword(tracer, patched_uvloop):
    """Callbacks passed by keyword must not break scheduling."""
    loop = patched_uvloop.new_event_loop()
    switches = []
    calls = []

    def record_context_switch():
        switches.append(tracer.context_provider.active())

    def callback():
        calls.append(len(calls))
        if len(calls) == 3:
            loop.stop()

    core.on("python.context.switch", record_context_switch)
    try:
        loop.call_soon(callback=callback)
        loop.call_later(0, callback=callback)
        loop.call_at(loop.time(), callback=callback)
        loop.run_forever()
    finally:
        core.reset_listeners("python.context.switch", record_context_switch)
        loop.close()
        tracer.context_provider.activate(None)

    assert len(calls) == 3
    assert switches


@pytest.mark.skipif(not hasattr(asyncio, "eager_task_factory"), reason="eager tasks require Python 3.12+")
@_requires_context_switch_instrumentation
def test_uvloop_eager_task_events_restore_caller(tracer, patched_uvloop):
    loop = patched_uvloop.new_event_loop()
    span = Span("eager")
    caller = Span("caller")
    context = _captured_context(tracer, span)
    switches = []
    observed = []

    def record_context_switch():
        switches.append(tracer.context_provider.active())

    async def eager(expected):
        assert tracer.context_provider.active() is expected
        observed.append((len(switches) - 1, expected))
        return "done"

    async def create_tasks():
        loop.set_task_factory(getattr(asyncio, "eager_task_factory"))
        tracer.context_provider.activate(caller)
        assert loop.create_task(eager(span), context=context).result() == "done"
        assert loop.create_task(eager(None), context=contextvars.Context()).result() == "done"

    core.on("python.context.switch", record_context_switch)
    try:
        loop.run_until_complete(create_tasks())
    finally:
        core.reset_listeners("python.context.switch", record_context_switch)
        loop.close()
        span.finish()
        caller.finish()
        tracer.context_provider.activate(None)

    for event_index, expected in observed:
        assert switches[event_index : event_index + 2] == [expected, caller]


@pytest.mark.skipif(
    sys.version_info < (3, 11),
    reason="uvloop runs the task factory inside the given Context before 3.11, which raises unpatched too",
)
@_requires_context_switch_instrumentation
def test_uvloop_create_task_from_inside_the_task_context(tracer, patched_uvloop):
    """``create_task`` must work when the caller already runs in the task Context."""
    loop = patched_uvloop.new_event_loop()
    span = Span("task")
    context = _captured_context(tracer, span)
    switches = []
    observed = []

    def record_context_switch():
        switches.append(tracer.context_provider.active())

    async def worker():
        observed.append(tracer.context_provider.active())
        return "done"

    core.on("python.context.switch", record_context_switch)
    try:
        loop.set_task_factory(lambda loop, coro, **kwargs: asyncio.Task(coro, loop=loop, **kwargs))
        task = context.run(loop.create_task, worker(), context=context)
        assert loop.run_until_complete(task) == "done"
    finally:
        core.reset_listeners("python.context.switch", record_context_switch)
        loop.close()
        span.finish()
        tracer.context_provider.activate(None)

    assert observed == [span]
    assert switches


@_requires_context_switch_instrumentation
def test_uvloop_run_restores_caller_context(tracer, patched_uvloop):
    span = Span("runner")
    switches = []

    def record_context_switch():
        switches.append(tracer.context_provider.active())

    async def exercise():
        await asyncio.sleep(0)

    core.on("python.context.switch", record_context_switch)
    try:
        tracer.context_provider.activate(span)
        patched_uvloop.run(exercise())
        assert switches[-1] is span
    finally:
        core.reset_listeners("python.context.switch", record_context_switch)
        span.finish()
        tracer.context_provider.activate(None)
