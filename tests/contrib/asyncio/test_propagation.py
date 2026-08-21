import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from contextvars import ContextVar
from contextvars import copy_context
import sys
import threading
import time

import pytest

from ddtrace._trace.provider import DefaultContextProvider
from ddtrace.contrib.internal.asyncio.patch import patch
from ddtrace.contrib.internal.asyncio.patch import unpatch
from ddtrace.internal import core
from ddtrace.internal.constants import PYTHON_CONTEXT_SWITCH_EVENT
from ddtrace.internal.constants import PYTHON_CONTEXT_WATCHER_REGISTERED
from ddtrace.internal.wrapping import is_wrapped
from ddtrace.trace import Context


@pytest.fixture
def patched_asyncio():
    was_patched = getattr(asyncio, "_datadog_patch", False)
    watcher_state = core.root.get_item(PYTHON_CONTEXT_WATCHER_REGISTERED)

    def listener():
        pass

    unpatch()
    if watcher_state is None:
        # Exercise the Linux fallback on platforms where OTel thread-context publication is unavailable.
        core.root.set_item(PYTHON_CONTEXT_WATCHER_REGISTERED, False)
    core.on(PYTHON_CONTEXT_SWITCH_EVENT, listener)
    try:
        patch()
        yield
    finally:
        unpatch()
        core.reset_listeners(PYTHON_CONTEXT_SWITCH_EVENT, listener)
        if watcher_state is None:
            core.root.discard_local_item(PYTHON_CONTEXT_WATCHER_REGISTERED)
        else:
            core.root.set_item(PYTHON_CONTEXT_WATCHER_REGISTERED, watcher_state)
        if was_patched:
            patch()


@pytest.fixture
def python_context_fallback(patched_asyncio):
    """Skips unless context switches are published by the Python fallback rather than natively."""
    if not is_wrapped(asyncio.Handle._run):
        pytest.skip("the native context watcher is active")


@pytest.fixture
def failing_listener():
    """Registers a context switch listener that raises, and yields the type it raises."""

    class Boom(BaseException):
        pass

    def raise_boom():
        raise Boom()

    core.on(PYTHON_CONTEXT_SWITCH_EVENT, raise_boom)
    try:
        yield Boom
    finally:
        core.reset_listeners(PYTHON_CONTEXT_SWITCH_EVENT, raise_boom)


@asynccontextmanager
async def collected_loop_exceptions():
    """The contexts the running loop reports while the block runs, kept out of the default handler."""
    loop = asyncio.get_running_loop()
    original_handler = loop.get_exception_handler()
    handled = []

    loop.set_exception_handler(lambda _loop, context: handled.append(context))
    try:
        yield handled
    finally:
        loop.set_exception_handler(original_handler)


@pytest.fixture
def switches(tracer):
    """The trace context active at each published context switch, in publication order."""
    switches = []

    def record_context_switch():
        switches.append(tracer.context_provider.active())

    core.on(PYTHON_CONTEXT_SWITCH_EVENT, record_context_switch)
    try:
        yield switches
    finally:
        core.reset_listeners(PYTHON_CONTEXT_SWITCH_EVENT, record_context_switch)


def test_event_loop_unpatch(tracer):
    # DEV: wrapping mutates __code__ in place, so identity comparisons against the original
    # functions always hold. is_wrapped is the only way to observe the patched state.
    was_patched = getattr(asyncio, "_datadog_patch", False)
    unpatch()
    try:
        patch()
        fallback_expected = core.root.get_item(PYTHON_CONTEXT_WATCHER_REGISTERED) is False
        assert is_wrapped(asyncio.BaseEventLoop.create_task)
        assert is_wrapped(asyncio.Handle._run) is fallback_expected
        assert is_wrapped(asyncio.to_thread) is fallback_expected

        # ensures that the event loop can be unpatched
        unpatch()
        assert isinstance(tracer.context_provider, DefaultContextProvider)
        assert not is_wrapped(asyncio.BaseEventLoop.create_task)
        assert not is_wrapped(asyncio.Handle._run)
        assert not is_wrapped(asyncio.to_thread)
    finally:
        unpatch()
        if was_patched:
            patch()


@pytest.mark.subprocess(env={"DD_TRACE_OTEL_CTX_ENABLED": "false"})
def test_context_switch_instrumentation_not_installed_when_disabled():
    import asyncio

    from ddtrace.contrib.internal.asyncio.patch import patch
    from ddtrace.contrib.internal.asyncio.patch import unpatch
    from ddtrace.internal import core
    from ddtrace.internal.constants import PYTHON_CONTEXT_SWITCH_EVENT
    from ddtrace.internal.constants import PYTHON_CONTEXT_WATCHER_REGISTERED
    from ddtrace.internal.wrapping import is_wrapped

    assert not core.has_listeners(PYTHON_CONTEXT_SWITCH_EVENT)
    assert core.root.get_item(PYTHON_CONTEXT_WATCHER_REGISTERED) is None
    patch()
    try:
        assert not is_wrapped(asyncio.Handle._run)
        assert not is_wrapped(asyncio.to_thread)
    finally:
        unpatch()


@pytest.mark.skipif(
    sys.platform != "linux" or sys.implementation.name != "cpython" or sys.version_info < (3, 14),
    reason="requires the CPython 3.14 Linux context watcher",
)
@pytest.mark.subprocess(env={"_DD_GLOBAL_TRACER_INIT": "false"})
def test_context_watcher_slot_exhaustion_uses_python_fallback():
    import asyncio
    import ctypes
    import sys
    import threading

    assert "ddtrace.internal.native._native" not in sys.modules

    callback_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_uint, ctypes.py_object)
    callback = callback_type(lambda event, obj: 0)
    add_watcher = ctypes.pythonapi.PyContext_AddWatcher
    add_watcher.argtypes = [callback_type]
    add_watcher.restype = ctypes.c_int
    clear_watcher = ctypes.pythonapi.PyContext_ClearWatcher
    clear_watcher.argtypes = [ctypes.c_int]
    clear_watcher.restype = ctypes.c_int

    watcher_ids = []
    for _ in range(64):
        try:
            watcher_ids.append(add_watcher(callback))
        except RuntimeError:
            break
    else:
        raise AssertionError("context watcher slots were not exhausted")

    try:
        import ddtrace
        from ddtrace.contrib.internal.asyncio.patch import patch
        from ddtrace.contrib.internal.asyncio.patch import unpatch
        from ddtrace.internal import core
        from ddtrace.internal.constants import PYTHON_CONTEXT_SWITCH_EVENT
        from ddtrace.internal.constants import PYTHON_CONTEXT_WATCHER_REGISTERED
        from ddtrace.internal.wrapping import is_wrapped
        from ddtrace.trace import tracer

        # Tracer startup was delayed until the watcher slots were exhausted.
        # Restore the normal top-level binding used by Pin.enabled().
        ddtrace.tracer = tracer

        assert core.root.get_item(PYTHON_CONTEXT_WATCHER_REGISTERED) is False
        assert core.has_listeners(PYTHON_CONTEXT_SWITCH_EVENT)

        switches = []
        worker_ids = []

        def record_context_switch():
            switches.append((threading.get_ident(), tracer.context_provider.active()))

        def worker():
            worker_ids.append(threading.get_ident())

        async def exercise_fallback():
            with tracer.trace("parent") as parent:
                await asyncio.to_thread(worker)
            return parent

        core.on(PYTHON_CONTEXT_SWITCH_EVENT, record_context_switch)
        patch()
        loop = asyncio.new_event_loop()
        try:
            assert is_wrapped(asyncio.Handle._run)
            assert is_wrapped(asyncio.to_thread)
            parent = loop.run_until_complete(exercise_fallback())
        finally:
            loop.run_until_complete(loop.shutdown_default_executor())
            loop.close()
            unpatch()
            core.reset_listeners(PYTHON_CONTEXT_SWITCH_EVENT, record_context_switch)

        assert not is_wrapped(asyncio.Handle._run)
        assert not is_wrapped(asyncio.to_thread)
        assert len(worker_ids) == 1
        assert [context for ident, context in switches if ident == worker_ids[0]] == [parent, None]
    finally:
        for watcher_id in watcher_ids:
            assert clear_watcher(watcher_id) == 0


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
                "eager_start",
                marks=pytest.mark.skipif(
                    sys.version_info < (3, 14), reason="loop.create_task(eager_start=...) requires Python 3.14+"
                ),
            ),
        ]
    },
)
def test_create_task_publishes_only_the_context_switches_of_eager_tasks():
    """A task whose first step runs inside create_task publishes that step's switch, others don't.

    Runs in a subprocess with OTel thread context disabled so that the Python fallback is the one
    publishing, even on CPython 3.14 Linux where the native watcher is registered process-wide and
    cannot be unregistered.
    """
    import asyncio
    from contextvars import copy_context
    import os

    from ddtrace.contrib.internal.asyncio.patch import patch
    from ddtrace.contrib.internal.asyncio.patch import unpatch
    from ddtrace.internal import core
    from ddtrace.internal.constants import PYTHON_CONTEXT_SWITCH_EVENT
    from ddtrace.internal.constants import PYTHON_CONTEXT_WATCHER_REGISTERED
    from ddtrace.trace import tracer

    mode = os.environ["TASK_MODE"]
    starts_eagerly = mode != "non_eager_factory"
    switches = []

    def record_context_switch():
        switches.append(tracer.context_provider.active())

    def non_eager_task_factory(loop, coro, **kwargs):
        return asyncio.Task(coro, loop=loop, **kwargs)

    span = tracer.trace("eager")
    task_kwargs = {}
    if starts_eagerly:
        # the inline step must publish the task's own context, so hand it one the caller does not have
        task_kwargs["context"] = copy_context()
        tracer.context_provider.activate(None)
    if mode == "eager_start":
        task_kwargs["eager_start"] = True

    async def child():
        return "done"

    async def main():
        loop = asyncio.get_running_loop()
        if mode == "eager_factory":
            loop.set_task_factory(asyncio.eager_task_factory)
        elif mode == "non_eager_factory":
            loop.set_task_factory(non_eager_task_factory)

        # running the loop publishes switches too, only what create_task itself publishes matters
        switches.clear()
        task = loop.create_task(child(), **task_kwargs)
        assert switches == ([span, None] if starts_eagerly else [])
        assert await task == "done"

    core.root.set_item(PYTHON_CONTEXT_WATCHER_REGISTERED, False)
    core.on(PYTHON_CONTEXT_SWITCH_EVENT, record_context_switch)
    unpatch()
    patch()
    try:
        asyncio.run(main())
    finally:
        unpatch()
        core.reset_listeners(PYTHON_CONTEXT_SWITCH_EVENT, record_context_switch)
        span.finish()


@pytest.mark.skipif(sys.version_info < (3, 12), reason="eager tasks require Python 3.12+")
@pytest.mark.subprocess(env={"DD_TRACE_OTEL_CTX_ENABLED": "false"})
def test_create_task_publishes_eager_switch_even_when_the_pin_is_disabled():
    """The eager step's switch does not depend on the Pin: it isn't trace propagation, it's a fact
    about which context is active right now, and thread-context sync needs it regardless.

    Runs in a subprocess with OTel thread context disabled so that the Python fallback is the one
    publishing, even on CPython 3.14 Linux where the native watcher is registered process-wide and
    cannot be unregistered.
    """
    import asyncio

    import ddtrace
    from ddtrace.contrib.internal.asyncio.patch import patch
    from ddtrace.contrib.internal.asyncio.patch import unpatch
    from ddtrace.internal import core
    from ddtrace.internal.constants import PYTHON_CONTEXT_SWITCH_EVENT
    from ddtrace.internal.constants import PYTHON_CONTEXT_WATCHER_REGISTERED

    switches = []

    def record_context_switch():
        switches.append(True)

    async def child():
        return "done"

    async def main():
        loop = asyncio.get_running_loop()
        loop.set_task_factory(asyncio.eager_task_factory)
        switches.clear()
        task = loop.create_task(child())
        assert switches == [True, True]
        assert await task == "done"

    watcher_state = core.root.get_item(PYTHON_CONTEXT_WATCHER_REGISTERED)
    core.root.set_item(PYTHON_CONTEXT_WATCHER_REGISTERED, False)
    core.on(PYTHON_CONTEXT_SWITCH_EVENT, record_context_switch)
    unpatch()
    patch()
    ddtrace.tracer.enabled = False
    try:
        asyncio.run(main())
    finally:
        ddtrace.tracer.enabled = True
        unpatch()
        core.reset_listeners(PYTHON_CONTEXT_SWITCH_EVENT, record_context_switch)
        if watcher_state is None:
            core.root.discard_local_item(PYTHON_CONTEXT_WATCHER_REGISTERED)
        else:
            core.root.set_item(PYTHON_CONTEXT_WATCHER_REGISTERED, watcher_state)


@pytest.mark.asyncio
async def test_to_thread_forwards_func_keyword_argument(python_context_fallback):
    """asyncio.to_thread takes func positionally only, so "func" belongs to the target."""

    def worker(*args, **kwargs):
        return args, kwargs

    assert await asyncio.to_thread(worker, 1, func="x") == ((1,), {"func": "x"})


@pytest.mark.asyncio
async def test_context_switch_listener_failure_does_not_kill_the_loop(python_context_fallback, failing_listener):
    async with collected_loop_exceptions() as handled:
        await asyncio.sleep(0)

    assert handled
    assert all(isinstance(context["exception"], failing_listener) for context in handled)


def test_context_switch_entry_listener_failure_reaches_the_custom_exception_handler(python_context_fallback):
    loop = asyncio.new_event_loop()
    phase = ContextVar("phase", default="ambient")
    callback_context = copy_context()
    callback_context.run(phase.set, "handle")
    handled = []
    secondary = []

    class ListenerFailure(Exception):
        pass

    def failing_listener():
        raise ListenerFailure(phase.get())

    loop.set_exception_handler(lambda _loop, context: handled.append(context))
    loop.default_exception_handler = lambda context: secondary.append(context)
    core.on(PYTHON_CONTEXT_SWITCH_EVENT, failing_listener)
    try:
        asyncio.Handle(lambda: None, (), loop, context=callback_context)._run()
    finally:
        core.reset_listeners(PYTHON_CONTEXT_SWITCH_EVENT, failing_listener)
        loop.close()

    assert [str(context["exception"]) for context in handled] == ["handle", "ambient"]
    assert secondary == []


def test_callback_failure_reports_the_application_callback(python_context_fallback):
    loop = asyncio.new_event_loop()
    handled = []

    def application_callback():
        raise RuntimeError("application failure")

    def exception_handler(_loop, context):
        handled.append((context, context["handle"]._callback))

    loop.set_exception_handler(exception_handler)
    handle = asyncio.Handle(application_callback, (), loop, context=copy_context())
    try:
        handle._run()
    finally:
        loop.close()

    assert len(handled) == 1
    context, reported_callback = handled[0]
    assert context["exception"].args == ("application failure",)
    assert "application_callback" in context["message"]
    assert "_callback_with_entry_dispatch" not in context["message"]
    assert reported_callback is application_callback


@pytest.mark.asyncio
async def test_context_switch_listener_failure_does_not_reach_the_to_thread_worker(
    python_context_fallback, failing_listener
):
    """A worker thread has no loop to report the failure to, so it is logged and the worker still runs."""
    ran = []

    def worker():
        ran.append(True)
        return "result"

    async with collected_loop_exceptions():
        assert await asyncio.to_thread(worker) == "result"

    assert ran


@pytest.mark.skipif(sys.version_info < (3, 12), reason="eager tasks require Python 3.12+")
@pytest.mark.asyncio
async def test_context_switch_listener_failure_does_not_break_an_eager_task(python_context_fallback, failing_listener):
    """The inline switch of an eagerly started task is published under the same guard as loop callbacks."""

    async def child():
        return "done"

    loop = asyncio.get_running_loop()
    original_factory = loop.get_task_factory()

    loop.set_task_factory(asyncio.eager_task_factory)
    try:
        async with collected_loop_exceptions() as handled:
            assert await loop.create_task(child()) == "done"
    finally:
        loop.set_task_factory(original_factory)

    assert any(isinstance(context["exception"], failing_listener) for context in handled)


@pytest.mark.asyncio
async def test_event_loop_double_patch(tracer, test_spans):
    # ensures that double patching will not double instrument
    # the event loop
    was_patched = getattr(asyncio, "_datadog_patch", False)
    try:
        patch()
        patch()
        await test_tasks_chaining(tracer, test_spans)
    finally:
        unpatch()
        if was_patched:
            patch()


@pytest.mark.asyncio
async def test_tasks_chaining(tracer, test_spans):
    # ensures that the context is propagated between different tasks
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
    # ensures that if a new Context is activated, a trace
    # with the Context arguments is created
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
async def test_context_switch_events_track_task_switches(tracer, python_context_fallback, switches):
    first_started = asyncio.Event()
    resume_first = asyncio.Event()
    first_resumed = asyncio.Event()
    finish_first = asyncio.Event()

    async def first():
        with tracer.trace("first") as span:
            first_started.set()
            await resume_first.wait()
            try:
                assert switches[-1] is span
            finally:
                first_resumed.set()
            await finish_first.wait()

    async def second():
        await first_started.wait()
        with tracer.trace("second") as span:
            resume_first.set()
            await first_resumed.wait()
            try:
                assert switches[-1] is span
                assert switches[-2] is None
            finally:
                finish_first.set()

    await asyncio.gather(first(), second())


@pytest.mark.asyncio
async def test_to_thread_context_switch_events(tracer, python_context_fallback):
    switches = []
    worker_id = None

    def record_context_switch():
        switches.append((threading.get_ident(), tracer.context_provider.active()))

    def worker():
        nonlocal worker_id
        worker_id = threading.get_ident()

    core.on(PYTHON_CONTEXT_SWITCH_EVENT, record_context_switch)
    try:
        with tracer.trace("parent") as parent:
            await asyncio.to_thread(worker)
    finally:
        core.reset_listeners(PYTHON_CONTEXT_SWITCH_EVENT, record_context_switch)

    assert worker_id is not None
    assert [context for ident, context in switches if ident == worker_id] == [parent, None]


def test_to_thread_restores_ambient_worker_context(tracer, python_context_fallback):
    switches = []
    ambient_worker = tracer.start_span("ambient-worker")
    executor = ThreadPoolExecutor(max_workers=1, initializer=lambda: tracer.context_provider.activate(ambient_worker))
    loop = asyncio.new_event_loop()
    loop.set_default_executor(executor)

    def record_context_switch():
        switches.append((threading.get_ident(), tracer.context_provider.active()))

    def active_context():
        return threading.get_ident(), tracer.context_provider.active()

    async def exercise():
        with tracer.trace("parent") as parent:
            copied = await asyncio.to_thread(active_context)
        restored = await loop.run_in_executor(None, active_context)
        return parent, copied, restored

    core.on(PYTHON_CONTEXT_SWITCH_EVENT, record_context_switch)
    try:
        parent, copied, restored = loop.run_until_complete(exercise())
    finally:
        loop.run_until_complete(loop.shutdown_default_executor())
        loop.close()
        core.reset_listeners(PYTHON_CONTEXT_SWITCH_EVENT, record_context_switch)
        ambient_worker.finish()

    assert copied == (restored[0], parent)
    assert restored[1] is ambient_worker
    assert [context for ident, context in switches if ident == restored[0]] == [parent, ambient_worker]


@pytest.mark.skipif(sys.platform != "linux", reason="OTel thread context is only published on Linux")
@pytest.mark.asyncio
async def test_to_thread_syncs_native_otel_context(tracer, patched_asyncio):
    import ctypes

    from ddtrace.internal.native import _native

    class ThreadContextRecord(ctypes.Structure):
        _fields_ = [
            ("trace_id", ctypes.c_ubyte * 16),
            ("span_id", ctypes.c_ubyte * 8),
            ("valid", ctypes.c_ubyte),
            ("trace_flags", ctypes.c_ubyte),
        ]

    native_library = ctypes.CDLL(_native.__file__)

    def published_span_id():
        slot = ctypes.c_void_p.in_dll(native_library, "otel_thread_ctx_v1")
        if slot.value is None:
            return None

        record = ThreadContextRecord.from_address(slot.value)
        if not record.valid:
            return None
        return int.from_bytes(record.span_id, byteorder="big")

    with tracer.trace("parent") as parent:
        assert await asyncio.to_thread(published_span_id) == parent.span_id

    assert await asyncio.to_thread(published_span_id) is None


@pytest.mark.asyncio
async def test_context_switch_event_skips_finished_span(tracer, python_context_fallback, switches):
    loop = asyncio.get_running_loop()
    callback_finished = loop.create_future()

    with tracer.trace("parent") as parent:
        with tracer.trace("child") as child:

            def callback():
                callback_finished.set_result(switches[-1] if switches else None)

            loop.call_soon(callback)

        switches.clear()
        assert await callback_finished is parent
        assert parent in switches
        assert child not in switches
