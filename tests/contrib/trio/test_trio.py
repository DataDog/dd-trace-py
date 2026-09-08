from contextvars import ContextVar
import sys
import threading
from types import SimpleNamespace

import pytest
import trio
import trio.abc

from ddtrace.contrib.internal.trio import patch as trio_patch
from ddtrace.internal import core
from ddtrace.internal._context_watcher import PYTHON_CONTEXT_SWITCH_EVENT
from ddtrace.internal.opentelemetry.thread_context import register_otel_thread_context_listener
from tests.tracer.test_otel_thread_context import _published_context


@pytest.fixture
def clean_patch(monkeypatch):
    """Restore the Trio patch state after a test changes the fallback gate."""
    was_patched = getattr(trio, "_datadog_patch", False)
    original_gate = trio_patch.context_switches_require_fallback
    trio_patch.unpatch()
    try:
        yield
    finally:
        trio_patch.unpatch()
        monkeypatch.setattr(trio_patch, "context_switches_require_fallback", original_gate)
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
    monkeypatch.setattr(trio_patch, "core", SimpleNamespace(dispatch=record_context_switch))
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


def test_patch_inside_active_run_instruments_current_run(clean_patch, monkeypatch):
    """Late patching uses Trio's public API to instrument the active run."""
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

    monkeypatch.setattr(trio_patch, "context_switches_require_fallback", lambda: True)
    monkeypatch.setattr(trio_patch, "core", SimpleNamespace(dispatch=record_context_switch))
    trio.run(exercise)


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
    monkeypatch.setattr(trio_patch, "core", SimpleNamespace(dispatch=record_context_switch))
    trio_patch.patch()
    trio.run(exercise)

    assert switches == ["caller", None]


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
