from contextvars import ContextVar
import inspect
import queue
import sys
import threading
from types import SimpleNamespace

import pytest
import trio
import trio.abc

from ddtrace.contrib.internal.trio import patch as trio_patch
from ddtrace.internal import _context_watcher as context_watcher
from ddtrace.internal import core
from ddtrace.internal._context_watcher import PYTHON_CONTEXT_SWITCH_EVENT
from ddtrace.internal.opentelemetry.thread_context import register_otel_thread_context_listener
from tests.tracer.test_otel_thread_context import _published_context


@pytest.fixture
def clean_patch(monkeypatch):
    """Restore the Trio patch state after a test changes the fallback gate."""
    was_patched = getattr(trio, "_datadog_patch", False)
    trio_patch.unpatch()
    try:
        yield
    finally:
        # Undo every monkeypatch.setattr the test made (e.g. stubbing context_watcher.core or
        # the fallback gate) before touching patch state: monkeypatch's own teardown runs after
        # this fixture's, so unpatch()/patch() below would otherwise still see the test's stubs.
        monkeypatch.undo()
        trio_patch.unpatch()
        if was_patched:
            trio_patch.patch()


def test_task_switches_publish_resumed_context(clean_patch, monkeypatch):
    """Publish each task's context before it resumes and ambient context after it yields."""
    marker = ContextVar("marker", default=None)
    switches = []

    def record_context_switch(event):
        assert event == PYTHON_CONTEXT_SWITCH_EVENT
        switches.append(marker.get())

    async def child(name):
        marker.set(name)
        await trio.lowlevel.checkpoint()
        assert switches[-1] == name

    async def exercise():
        async with trio.open_nursery() as nursery:
            nursery.start_soon(child, "first")
            nursery.start_soon(child, "second")

    monkeypatch.setattr(trio_patch, "context_switches_require_fallback", lambda: True)
    monkeypatch.setattr(
        trio_patch, "core", SimpleNamespace(dispatch=record_context_switch, has_listeners=lambda event: True)
    )
    trio_patch.patch()
    trio.run(exercise)

    assert {"first", "second"}.issubset(switches)
    assert switches[-1] is None


def test_caller_instrument_is_preserved(clean_patch, monkeypatch):
    """Appending the context instrument does not replace caller instrumentation."""
    task_steps = []

    class CallerInstrument(trio.abc.Instrument):
        def before_task_step(self, task):
            task_steps.append(task.name)

    async def exercise():
        await trio.lowlevel.checkpoint()

    monkeypatch.setattr(trio_patch, "context_switches_require_fallback", lambda: True)
    trio_patch.patch()
    trio.run(exercise, instruments=(CallerInstrument(),))

    assert task_steps


def test_guest_run_publishes_task_contexts_and_restoration(clean_patch, monkeypatch):
    """A guest run publishes distinct task contexts, ambient restoration, and final cleanup."""
    marker = ContextVar("marker", default=None)
    switches = []
    todo = queue.Queue()

    def record_context_switch(event):
        assert event == PYTHON_CONTEXT_SWITCH_EVENT
        switches.append(marker.get())

    def schedule(callback):
        todo.put(("run", callback))

    def done_callback(run_outcome):
        todo.put(("done", run_outcome))

    async def child(name):
        marker.set(name)
        await trio.lowlevel.checkpoint()

    async def exercise():
        async with trio.open_nursery() as nursery:
            nursery.start_soon(child, "first")
            nursery.start_soon(child, "second")

    monkeypatch.setattr(trio_patch, "context_switches_require_fallback", lambda: True)
    monkeypatch.setattr(
        trio_patch, "core", SimpleNamespace(dispatch=record_context_switch, has_listeners=lambda event: True)
    )
    trio_patch.patch()
    trio.lowlevel.start_guest_run(
        exercise,
        run_sync_soon_threadsafe=schedule,
        run_sync_soon_not_threadsafe=schedule,
        done_callback=done_callback,
    )

    while True:
        operation, value = todo.get(timeout=5)
        if operation == "run":
            value()
        else:
            value.unwrap()
            break

    assert {"first", "second"}.issubset(switches)
    assert all(switches[index + 1] is None for index, value in enumerate(switches[:-1]) if value in {"first", "second"})
    assert switches[-1] is None


def test_patch_inside_active_run_instruments_current_run(clean_patch, monkeypatch):
    """Late unpatching publishes its pending restoration and then stops all task events."""
    marker = ContextVar("marker", default=None)
    switches = []

    def record_context_switch(event):
        assert event == PYTHON_CONTEXT_SWITCH_EVENT
        switches.append(marker.get())

    async def exercise():
        marker.set("task")
        trio_patch.patch()
        await trio.lowlevel.checkpoint()
        assert switches[-1] == "task"
        trio_patch.unpatch()
        await trio.lowlevel.checkpoint()
        assert switches[-1] is None
        switch_count = len(switches)
        await trio.lowlevel.checkpoint()
        assert len(switches) == switch_count

    monkeypatch.setattr(trio_patch, "context_switches_require_fallback", lambda: True)
    monkeypatch.setattr(
        trio_patch, "core", SimpleNamespace(dispatch=record_context_switch, has_listeners=lambda event: True)
    )
    trio.run(exercise)


def test_patch_unpatch_patch_inside_active_run_does_not_duplicate_events(clean_patch, monkeypatch):
    """Re-enabling the singleton in one run preserves one entry and restoration per checkpoint."""
    marker = ContextVar("marker", default=None)
    switches = []

    def record_context_switch(event):
        assert event == PYTHON_CONTEXT_SWITCH_EVENT
        switches.append(marker.get())

    async def exercise():
        marker.set("task")

        trio_patch.patch()
        switches.clear()
        await trio.lowlevel.checkpoint()
        assert switches.count("task") == 1
        assert switches[-1] == "task"

        trio_patch.unpatch()
        switches.clear()
        await trio.lowlevel.checkpoint()
        assert switches == [None]

        trio_patch.patch()
        switches.clear()
        await trio.lowlevel.checkpoint()
        assert switches.count("task") == 1
        assert switches[-1] == "task"

        trio_patch.patch()
        switches.clear()
        await trio.lowlevel.checkpoint()
        assert switches.count("task") == 1
        assert switches[-1] == "task"

    monkeypatch.setattr(trio_patch, "context_switches_require_fallback", lambda: True)
    monkeypatch.setattr(
        trio_patch, "core", SimpleNamespace(dispatch=record_context_switch, has_listeners=lambda event: True)
    )
    trio.run(exercise)


def test_unpatch_from_foreign_thread_retires_active_instrument(tracer, clean_patch, monkeypatch):
    """Foreign-thread unpatching pairs the active entry and suppresses every later task step."""
    marker = ContextVar("marker", default=None)
    task_blocked = threading.Event()
    release_task = threading.Event()
    switches = []
    thread_errors = []
    final_published_contexts = []
    listeners = None

    def record_context_switch(event):
        assert event == PYTHON_CONTEXT_SWITCH_EVENT
        switches.append(marker.get())
        core.dispatch(event)

    async def exercise():
        with tracer.trace("task"):
            marker.set("task")
            task_blocked.set()
            assert release_task.wait(timeout=5)
            await trio.lowlevel.checkpoint()
            assert switches[-1] is None
            switch_count = len(switches)
            await trio.lowlevel.checkpoint()
            assert len(switches) == switch_count

    def run_trio():
        try:
            trio.run(exercise)
            if sys.platform == "linux":
                final_published_contexts.append(_published_context())
        except BaseException as exc:
            thread_errors.append(exc)

    monkeypatch.setattr(trio_patch, "context_switches_require_fallback", lambda: True)
    monkeypatch.setattr(
        trio_patch, "core", SimpleNamespace(dispatch=record_context_switch, has_listeners=lambda event: True)
    )
    if sys.platform == "linux":
        listeners = register_otel_thread_context_listener(tracer)
        assert listeners is not None

    trio_patch.patch()
    run_thread = threading.Thread(target=run_trio)
    try:
        run_thread.start()
        assert task_blocked.wait(timeout=5)
        trio_patch.unpatch()
        release_task.set()
        run_thread.join(timeout=5)
    finally:
        release_task.set()
        run_thread.join(timeout=5)
        if listeners is not None:
            activation_listener, context_switch_listener = listeners
            core.reset_listeners("ddtrace.context_provider.activate", activation_listener)
            core.reset_listeners(PYTHON_CONTEXT_SWITCH_EVENT, context_switch_listener)

    assert not run_thread.is_alive()
    assert not thread_errors
    assert switches[-1] is None
    if sys.platform == "linux":
        assert final_published_contexts == [None]


@pytest.mark.parametrize("fails", [False, True])
def test_run_sync_publishes_worker_context(clean_patch, monkeypatch, fails):
    """Publish entry and exit for successful and failing Trio workers."""
    marker = ContextVar("marker", default=None)
    main_thread = threading.get_ident()
    switches = []

    def record_context_switch(event):
        assert event == PYTHON_CONTEXT_SWITCH_EVENT
        if threading.get_ident() != main_thread:
            switches.append(marker.get())

    def worker():
        assert marker.get() == "caller"
        if fails:
            raise RuntimeError("worker failure")
        return "done"

    async def exercise():
        marker.set("caller")
        if fails:
            with pytest.raises(RuntimeError, match="worker failure"):
                await trio.to_thread.run_sync(worker)
        else:
            assert await trio.to_thread.run_sync(worker) == "done"

    monkeypatch.setattr(trio_patch, "context_switches_require_fallback", lambda: True)
    monkeypatch.setattr(
        context_watcher, "core", SimpleNamespace(dispatch=record_context_switch, has_listeners=lambda event: True)
    )
    trio_patch.patch()
    trio.run(exercise)

    assert switches == ["caller", None]


def test_run_sync_preserves_default_worker_name(clean_patch, monkeypatch):
    """Trio derives its default worker name from the original wrapped callable."""
    if "thread_name" not in inspect.signature(trio.to_thread.run_sync).parameters:
        pytest.skip("Trio does not support explicit worker thread names")

    def named_worker():
        return threading.current_thread().name

    async def exercise():
        worker_name = await trio.to_thread.run_sync(named_worker)
        assert worker_name.startswith("named_worker from ")

    monkeypatch.setattr(trio_patch, "context_switches_require_fallback", lambda: True)
    trio_patch.patch()
    trio.run(exercise)


@pytest.mark.parametrize("fails", [False, True])
def test_from_thread_run_sync_publishes_inferred_callback_context(clean_patch, monkeypatch, fails):
    """Worker callbacks publish their copied context and restore ambient Trio state on every exit."""
    marker = ContextVar("marker", default=None)
    main_thread = threading.get_ident()
    switches = []

    def record_context_switch(event):
        assert event == PYTHON_CONTEXT_SWITCH_EVENT
        if threading.get_ident() == main_thread:
            switches.append(marker.get())

    def callback():
        assert marker.get() == "worker"
        if fails:
            raise RuntimeError("callback failure")
        return "done"

    def worker():
        marker.set("worker")
        return trio.from_thread.run_sync(callback)

    async def exercise():
        marker.set("trio-task")
        if fails:
            with pytest.raises(RuntimeError, match="callback failure"):
                await trio.to_thread.run_sync(worker)
        else:
            assert await trio.to_thread.run_sync(worker) == "done"

    monkeypatch.setattr(trio_patch, "context_switches_require_fallback", lambda: True)
    recorder = SimpleNamespace(dispatch=record_context_switch, has_listeners=lambda event: True)
    monkeypatch.setattr(trio_patch, "core", recorder)
    monkeypatch.setattr(context_watcher, "core", recorder)
    trio_patch.patch()
    trio.run(exercise)

    callback_index = switches.index("worker")
    assert switches.count("worker") == 1
    assert switches[callback_index + 1] is None
    assert switches[-1] is None


def test_from_thread_run_sync_publishes_foreign_callback_context(clean_patch, monkeypatch):
    """A foreign thread with an explicit token publishes its copied callback context and restoration."""
    marker = ContextVar("marker", default=None)
    main_thread = threading.get_ident()
    switches = []
    thread_errors = []

    def record_context_switch(event):
        assert event == PYTHON_CONTEXT_SWITCH_EVENT
        if threading.get_ident() == main_thread:
            switches.append(marker.get())

    def callback():
        assert marker.get() == "foreign"
        return "done"

    async def exercise():
        marker.set("trio-task")
        trio_token = trio.lowlevel.current_trio_token()

        def run_from_foreign_thread():
            try:
                marker.set("foreign")
                assert trio.from_thread.run_sync(callback, trio_token=trio_token) == "done"
            except BaseException as exc:
                thread_errors.append(exc)

        foreign_thread = threading.Thread(target=run_from_foreign_thread)
        foreign_thread.start()
        while foreign_thread.is_alive():
            await trio.sleep(0.01)
        foreign_thread.join()

    monkeypatch.setattr(trio_patch, "context_switches_require_fallback", lambda: True)
    monkeypatch.setattr(
        trio_patch, "core", SimpleNamespace(dispatch=record_context_switch, has_listeners=lambda event: True)
    )
    trio_patch.patch()
    trio.run(exercise)

    assert not thread_errors
    callback_index = switches.index("foreign")
    assert switches.count("foreign") == 1
    assert None in switches[callback_index + 1 :]
    assert switches[-1] is None


@pytest.mark.skipif(sys.platform != "linux", reason="OTel thread context is only published on Linux")
def test_native_thread_context_follows_task_switches(tracer, clean_patch):
    """The raw OTel thread context follows each resumed Trio task."""
    listeners = register_otel_thread_context_listener(tracer)
    assert listeners is not None
    activation_listener, context_switch_listener = listeners

    async def exercise():
        first_started = trio.Event()
        second_started = trio.Event()

        async def first():
            with tracer.trace("first") as span:
                expected = span.trace_id, span.span_id
                first_started.set()
                await second_started.wait()
                assert _published_context()[:2] == expected

        async def second():
            await first_started.wait()
            with tracer.trace("second") as span:
                expected = span.trace_id, span.span_id
                second_started.set()
                await trio.lowlevel.checkpoint()
                assert _published_context()[:2] == expected

        async with trio.open_nursery() as nursery:
            nursery.start_soon(first)
            nursery.start_soon(second)

    try:
        trio_patch.patch()
        trio.run(exercise)
        assert _published_context() is None
    finally:
        core.reset_listeners("ddtrace.context_provider.activate", activation_listener)
        core.reset_listeners(PYTHON_CONTEXT_SWITCH_EVENT, context_switch_listener)


@pytest.mark.skipif(sys.platform != "linux", reason="OTel thread context is only published on Linux")
def test_native_thread_context_follows_from_thread_callback(tracer, clean_patch):
    """A callback entering Trio publishes its copied worker context and leaves no final native state."""
    listeners = register_otel_thread_context_listener(tracer)
    assert listeners is not None
    activation_listener, context_switch_listener = listeners
    callback_states = []

    def callback():
        callback_states.append(_published_context())

    def worker():
        with tracer.trace("worker") as span:
            expected = span.trace_id, span.span_id
            trio.from_thread.run_sync(callback)
            return expected

    async def exercise():
        expected = await trio.to_thread.run_sync(worker)
        assert callback_states[0][:2] == expected

    try:
        trio_patch.patch()
        trio.run(exercise)
        assert _published_context() is None
    finally:
        core.reset_listeners("ddtrace.context_provider.activate", activation_listener)
        core.reset_listeners(PYTHON_CONTEXT_SWITCH_EVENT, context_switch_listener)


@pytest.mark.skipif(sys.platform != "linux", reason="OTel thread context is only published on Linux")
@pytest.mark.parametrize("fails", [False, True])
def test_native_thread_context_is_published_and_cleared_in_worker(tracer, clean_patch, fails):
    """Trio workers publish active context and clear it before thread reuse."""
    listeners = register_otel_thread_context_listener(tracer)
    assert listeners is not None
    activation_listener, context_switch_listener = listeners
    active_states = []

    def worker_state():
        return threading.get_ident(), _published_context()

    def worker():
        active_states.append(worker_state())
        if fails:
            raise RuntimeError("worker failure")

    async def exercise():
        limiter = trio.CapacityLimiter(1)
        with tracer.trace("worker") as span:
            expected = span.trace_id, span.span_id
            if fails:
                with pytest.raises(RuntimeError, match="worker failure"):
                    await trio.to_thread.run_sync(worker, limiter=limiter)
            else:
                await trio.to_thread.run_sync(worker, limiter=limiter)
            assert active_states[0][1][:2] == expected

        trio_patch.unpatch()
        idle_state = await trio.to_thread.run_sync(worker_state, limiter=limiter)
        assert idle_state[0] == active_states[0][0]
        assert idle_state[1] is None

    try:
        trio_patch.patch()
        trio.run(exercise)
    finally:
        core.reset_listeners("ddtrace.context_provider.activate", activation_listener)
        core.reset_listeners(PYTHON_CONTEXT_SWITCH_EVENT, context_switch_listener)
