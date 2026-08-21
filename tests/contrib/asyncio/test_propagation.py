import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from contextvars import copy_context
import sys
import threading
import time

import pytest

import ddtrace
from ddtrace._trace.provider import DefaultContextProvider
from ddtrace.contrib.internal.asyncio.patch import patch
from ddtrace.contrib.internal.asyncio.patch import unpatch
from ddtrace.internal import core
from ddtrace.internal.constants import PYTHON_CONTEXT_SWITCH_EVENT
from ddtrace.internal.wrapping import is_wrapped
from ddtrace.trace import Context
from tests.contrib.asyncio.utils import isolated_event_loop
from tests.contrib.asyncio.utils import preserve_asyncio_patch_state


@pytest.fixture
def asyncio_patch_state():
    """Restore asyncio patching after a test mutates it."""
    with preserve_asyncio_patch_state():
        yield


@pytest.fixture
def isolated_loop():
    """Provide an event loop without relying on pytest-asyncio's legacy event_loop fixture."""
    with isolated_event_loop() as loop:
        yield loop


@pytest.fixture
def python_context_fallback():
    """Patch the Python context-switch fallback and return a scoped listener registrar."""
    listeners = []

    def register(listener):
        core.on(PYTHON_CONTEXT_SWITCH_EVENT, listener)
        listeners.append(listener)

    with preserve_asyncio_patch_state():
        try:
            register(lambda: None)
            patch()
            if not is_wrapped(asyncio.Handle._run):
                pytest.skip("the native context watcher is active")
            yield register
        finally:
            for listener in reversed(listeners):
                core.reset_listeners(PYTHON_CONTEXT_SWITCH_EVENT, listener)


@pytest.fixture
def loop_exceptions():
    """Return a context manager that captures and then restores a loop's exception handler."""

    @contextmanager
    def capture(loop):
        original_handler = loop.get_exception_handler()
        handled = []
        loop.set_exception_handler(lambda _loop, context: handled.append(context))
        try:
            yield handled
        finally:
            loop.set_exception_handler(original_handler)

    return capture


@pytest.fixture
def context_switches(tracer, python_context_fallback):
    """Record the thread and active trace context for every published context switch."""
    switches = []

    def record_context_switch():
        switches.append((threading.get_ident(), tracer.context_provider.active()))

    python_context_fallback(record_context_switch)
    return switches


@pytest.mark.parametrize(
    "fallback_required",
    [False, True],
    ids=["native-or-unused", "fallback"],
)
def test_context_switch_wrappers_follow_runtime_capability(fallback_required, tracer, asyncio_patch_state, monkeypatch):
    """Only a required fallback installs switch wrappers, and unpatch removes every wrapper."""
    # DEV: wrapping mutates __code__ in place, so identity comparisons against the original
    # functions always hold. is_wrapped is the only way to observe the patched state.
    monkeypatch.setattr(
        "ddtrace.contrib.internal.asyncio.patch.context_switches_require_fallback", lambda: fallback_required
    )
    patch()
    assert is_wrapped(asyncio.BaseEventLoop.create_task)
    assert is_wrapped(asyncio.Handle._run) is fallback_required
    assert is_wrapped(asyncio.to_thread) is fallback_required

    unpatch()
    assert isinstance(tracer.context_provider, DefaultContextProvider)
    assert not is_wrapped(asyncio.BaseEventLoop.create_task)
    assert not is_wrapped(asyncio.Handle._run)
    assert not is_wrapped(asyncio.to_thread)


@pytest.mark.subprocess(
    env={"DD_TRACE_OTEL_CTX_ENABLED": "false"},
    parametrize={
        "TASK_MODE": [
            "non_eager_factory",
            pytest.param(
                "eager_factory",
                marks=pytest.mark.skipif(sys.version_info < (3, 12), reason="eager tasks require Python 3.12+"),
            ),
            pytest.param(
                "eager_factory_pin_disabled",
                marks=pytest.mark.skipif(sys.version_info < (3, 12), reason="eager tasks require Python 3.12+"),
            ),
            pytest.param(
                "eager_start",
                marks=pytest.mark.skipif(
                    sys.version_info < (3, 14), reason="loop.create_task(eager_start=...) requires Python 3.14+"
                ),
            ),
        ]
    },
)
def test_create_task_publishes_only_the_context_switches_of_eager_tasks():
    """Only eager task creation publishes inline switches, independently of the tracing Pin.

    Runs in a subprocess with OTel thread context disabled so that the Python fallback is the one
    publishing, even on CPython 3.14 Linux where the native watcher is registered process-wide and
    cannot be unregistered.
    """
    import asyncio
    from contextvars import copy_context
    import os

    import ddtrace
    from ddtrace.contrib.internal.asyncio.patch import patch
    from ddtrace.contrib.internal.asyncio.patch import unpatch
    from ddtrace.internal import core
    from ddtrace.internal.constants import PYTHON_CONTEXT_SWITCH_EVENT
    from ddtrace.trace import tracer

    mode = os.environ["TASK_MODE"]
    switches = []

    def record_context_switch():
        switches.append(tracer.context_provider.active())

    def non_eager_task_factory(loop, coro, **kwargs):
        return asyncio.Task(coro, loop=loop, **kwargs)

    eager_task_factory = getattr(asyncio, "eager_task_factory", None)
    task_factory, starts_eagerly, pin_enabled, task_kwargs = {
        "non_eager_factory": (non_eager_task_factory, False, True, {}),
        "eager_factory": (eager_task_factory, True, True, {}),
        "eager_factory_pin_disabled": (eager_task_factory, True, False, {}),
        "eager_start": (None, True, True, {"eager_start": True}),
    }[mode]

    span = tracer.trace("eager") if pin_enabled else None
    if starts_eagerly and span is not None:
        # the inline step must publish the task's own context, so hand it one the caller does not have
        task_kwargs["context"] = copy_context()
        tracer.context_provider.activate(None)

    async def child():
        return "done"

    async def main():
        loop = asyncio.get_running_loop()
        if task_factory is not None:
            loop.set_task_factory(task_factory)

        # running the loop publishes switches too, only what create_task itself publishes matters
        switches.clear()
        task = loop.create_task(child(), **task_kwargs)
        assert switches == ([span, None] if starts_eagerly else [])
        assert await task == "done"

    core.on(PYTHON_CONTEXT_SWITCH_EVENT, record_context_switch)
    unpatch()
    patch()
    ddtrace.tracer.enabled = pin_enabled
    try:
        asyncio.run(main())
    finally:
        ddtrace.tracer.enabled = True
        unpatch()
        core.reset_listeners(PYTHON_CONTEXT_SWITCH_EVENT, record_context_switch)
        if span is not None:
            span.finish()


def test_context_switch_entry_listener_failure_reaches_the_custom_exception_handler(
    python_context_fallback, isolated_loop, loop_exceptions
):
    """A Handle entry failure reaches the custom handler without re-entering its active Context."""
    phase = ContextVar("phase", default="ambient")
    callback_context = copy_context()
    callback_context.run(phase.set, "handle")
    callback_ran = []

    class ListenerFailure(Exception):
        pass

    def failing_listener():
        raise ListenerFailure(phase.get())

    python_context_fallback(failing_listener)
    with loop_exceptions(isolated_loop) as handled:
        asyncio.Handle(lambda: callback_ran.append(True), (), isolated_loop, context=callback_context)._run()

    assert callback_ran == [True]
    assert [str(context["exception"]) for context in handled] == ["handle", "ambient"]


def test_callback_failure_reports_the_application_callback(python_context_fallback, isolated_loop, loop_exceptions):
    """A failing Handle is reported as the application callback rather than the Datadog wrapper."""

    def application_callback():
        raise RuntimeError("application failure")

    with loop_exceptions(isolated_loop) as handled:
        handle = asyncio.Handle(application_callback, (), isolated_loop, context=copy_context())
        handle._run()

    assert len(handled) == 1
    context = handled[0]
    assert context["exception"].args == ("application failure",)
    assert "application_callback" in context["message"]
    assert "_callback_with_entry_dispatch" not in context["message"]
    assert context["handle"]._callback is application_callback


def test_context_switch_listener_cancellation_propagates(python_context_fallback, isolated_loop):
    """Cancellation raised by a listener remains a control-flow signal."""
    callback_ran = []

    def cancel():
        raise asyncio.CancelledError()

    python_context_fallback(cancel)
    handle = asyncio.Handle(lambda: callback_ran.append(True), (), isolated_loop, context=copy_context())
    with pytest.raises(asyncio.CancelledError):
        handle._run()

    assert callback_ran == []


@pytest.mark.asyncio
async def test_to_thread_listener_failure_is_reported_without_changing_result(python_context_fallback, loop_exceptions):
    """Worker listener failures reach the loop handler without replacing the worker result."""
    loop_thread = threading.get_ident()

    class ListenerFailure(Exception):
        pass

    def fail_in_worker():
        if threading.get_ident() != loop_thread:
            raise ListenerFailure()

    python_context_fallback(fail_in_worker)
    loop = asyncio.get_running_loop()
    with loop_exceptions(loop) as handled:
        result = await asyncio.to_thread(lambda: "result")
        await asyncio.sleep(0)

    assert result == "result"
    assert len(handled) == 2
    assert all(isinstance(context["exception"], ListenerFailure) for context in handled)


@pytest.mark.skipif(sys.version_info < (3, 12), reason="eager tasks require Python 3.12+")
@pytest.mark.asyncio
async def test_eager_task_listener_failure_is_reported_without_changing_result(
    python_context_fallback, loop_exceptions
):
    """An eager-entry listener failure reaches the loop handler without replacing the task result."""
    fail_listener = ContextVar("fail_listener", default=False)

    class ListenerFailure(Exception):
        pass

    def fail_in_task_context():
        if fail_listener.get():
            raise ListenerFailure()

    python_context_fallback(fail_in_task_context)

    async def child():
        return "result"

    loop = asyncio.get_running_loop()
    original_factory = loop.get_task_factory()
    task_context = copy_context()
    task_context.run(fail_listener.set, True)
    try:
        loop.set_task_factory(asyncio.eager_task_factory)
        with loop_exceptions(loop) as handled:
            result = await loop.create_task(child(), context=task_context)
    finally:
        loop.set_task_factory(original_factory)

    assert result == "result"
    assert len(handled) == 1
    assert isinstance(handled[0]["exception"], ListenerFailure)


@pytest.mark.asyncio
async def test_non_eager_task_factory_keeps_untraced_coroutine_fast_path(python_context_fallback):
    """A custom non-eager factory receives the original coroutine when tracing is disabled."""
    loop = asyncio.get_running_loop()
    original_factory = loop.get_task_factory()
    original_tracer_enabled = ddtrace.tracer.enabled
    received = []

    def non_eager_task_factory(loop, coro, **kwargs):
        received.append(coro)
        return asyncio.Task(coro, loop=loop, **kwargs)

    async def child():
        return "result"

    coro = child()
    try:
        loop.set_task_factory(non_eager_task_factory)
        ddtrace.tracer.enabled = False
        task = loop.create_task(coro)
        assert received == [coro]
        assert await task == "result"
    finally:
        ddtrace.tracer.enabled = original_tracer_enabled
        loop.set_task_factory(original_factory)


@pytest.mark.asyncio
async def test_event_loop_double_patch(tracer, test_spans, asyncio_patch_state):
    """Patching twice does not duplicate asyncio tracing instrumentation."""
    patch()
    patch()
    await test_tasks_chaining(tracer, test_spans)


@pytest.mark.asyncio
async def test_tasks_chaining(tracer, test_spans):
    """Sequential child tasks retain the trace parent captured by their creating task."""

    @tracer.wrap("spawn_task")
    async def coro_3():
        await asyncio.sleep(0.01)

    async def coro_2():
        # This will have a new context, first run will test that the
        # new context works correctly, second run will test if when we
        # pop off the last span on the context if it is still parented
        # correctly
        await coro_3()
        await coro_3()

    @tracer.wrap("main_task")
    async def coro_1():
        await asyncio.ensure_future(coro_2())

    await coro_1()

    traces = test_spans.pop_traces()
    assert len(traces) == 1
    spans = traces[0]
    assert len(spans) == 3
    main_task = spans[0]
    spawn_task1 = spans[1]
    spawn_task2 = spans[2]
    # check if the context has been correctly propagated
    assert spawn_task1.trace_id == main_task.trace_id
    assert spawn_task1.parent_id == main_task.span_id

    assert spawn_task2.trace_id == main_task.trace_id
    assert spawn_task2.parent_id == main_task.span_id


@pytest.mark.asyncio
async def test_concurrent_chaining(tracer, test_spans):
    """Concurrent tasks and later synchronous work remain children of the active parent span."""

    @tracer.wrap("f1")
    async def f1():
        await asyncio.sleep(0.01)

    @tracer.wrap("f2")
    async def f2():
        await asyncio.sleep(0.01)

    with tracer.trace("main_task"):
        await asyncio.gather(f1(), f2())
        # do additional synchronous work to confirm main context is
        # correctly handled
        with tracer.trace("main_task_child"):
            time.sleep(0.01)

    traces = test_spans.pop_traces()
    assert len(traces) == 1
    assert len(traces[0]) == 4
    main_task = traces[0][0]
    child_1 = traces[0][1]
    child_2 = traces[0][2]
    main_task_child = traces[0][3]
    # check if the context has been correctly propagated
    assert child_1.trace_id == main_task.trace_id
    assert child_1.parent_id == main_task.span_id
    assert child_2.trace_id == main_task.trace_id
    assert child_2.parent_id == main_task.span_id
    assert main_task_child.trace_id == main_task.trace_id
    assert main_task_child.parent_id == main_task.span_id


@pytest.mark.asyncio
async def test_propagation_with_new_context(tracer, test_spans):
    """A task started under an explicit trace Context uses its trace and parent identifiers."""
    ctx = Context(trace_id=100, span_id=101)
    tracer.context_provider.activate(ctx)

    with tracer.trace("async_task"):
        await asyncio.sleep(0.01)

    traces = test_spans.pop_traces()
    assert len(traces) == 1
    assert len(traces[0]) == 1
    span = traces[0][0]
    assert span.trace_id == 100
    assert span.parent_id == 101


@pytest.mark.asyncio
async def test_context_switch_events_track_task_switches(tracer, context_switches):
    """Suspending and resuming concurrent tasks publishes the context of the task that resumes."""
    first_started = asyncio.Event()
    resume_first = asyncio.Event()
    first_resumed = asyncio.Event()

    async def first():
        with tracer.trace("first") as span:
            first_started.set()
            await resume_first.wait()
            assert context_switches[-1][1] is span
            first_resumed.set()

    async def second():
        await first_started.wait()
        with tracer.trace("second") as span:
            resume_first.set()
            await first_resumed.wait()
            assert context_switches[-1][1] is span
            assert context_switches[-2][1] is None

    await asyncio.gather(first(), second())


@pytest.mark.parametrize("worker_has_context", [False, True], ids=["empty-worker", "ambient-worker"])
def test_to_thread_publishes_copied_and_restored_worker_context(
    worker_has_context, tracer, context_switches, isolated_loop
):
    """to_thread preserves target arguments and publishes copied then restored worker context."""
    ambient_worker = tracer.start_span("ambient-worker") if worker_has_context else None
    executor = ThreadPoolExecutor(
        max_workers=1,
        initializer=tracer.context_provider.activate,
        initargs=(ambient_worker,),
    )
    isolated_loop.set_default_executor(executor)

    def active_context(*args, **kwargs):
        return threading.get_ident(), tracer.context_provider.active(), args, kwargs

    async def exercise():
        with tracer.trace("parent") as parent:
            copied = await asyncio.to_thread(active_context, 1, func="x")
        restored = await isolated_loop.run_in_executor(None, active_context)
        return parent, copied, restored

    try:
        parent, copied, restored = isolated_loop.run_until_complete(exercise())
    finally:
        isolated_loop.run_until_complete(isolated_loop.shutdown_default_executor())
        if ambient_worker is not None:
            ambient_worker.finish()

    worker_id = restored[0]
    assert copied == (worker_id, parent, (1,), {"func": "x"})
    assert restored == (worker_id, ambient_worker, (), {})
    assert [context for ident, context in context_switches if ident == worker_id] == [parent, ambient_worker]


@pytest.mark.asyncio
async def test_context_switch_event_skips_finished_span(tracer, context_switches):
    """A callback captured under a finished child publishes its nearest unfinished parent."""
    loop = asyncio.get_running_loop()
    observed = []

    with tracer.trace("parent") as parent:
        with tracer.trace("child") as child:
            loop.call_soon(lambda: observed.append(context_switches[-1][1] if context_switches else None))

        context_switches.clear()
        await asyncio.sleep(0)
        assert observed == [parent]
        published = [context for _, context in context_switches]
        assert parent in published
        assert child not in published
