from contextvars import ContextVar
import threading
from types import SimpleNamespace

import pytest
import trio
import trio.abc

from ddtrace.contrib.internal.trio import patch as trio_patch
from ddtrace.internal._context_watcher import PYTHON_CONTEXT_SWITCH_EVENT


@pytest.fixture
def clean_patch(monkeypatch):
    """Restore the Trio patch state after a test changes the fallback gate."""
    was_patched = getattr(trio, "_datadog_patch", False)
    trio_patch.unpatch()
    try:
        yield
    finally:
        monkeypatch.undo()
        trio_patch.unpatch()
        if was_patched:
            trio_patch.patch()


def _install_fallback(monkeypatch, dispatch):
    monkeypatch.setattr(trio_patch, "context_switches_require_fallback", lambda: True)
    monkeypatch.setattr(trio_patch, "core", SimpleNamespace(dispatch=dispatch, has_listeners=lambda event: True))
    trio_patch.patch()


def test_task_switches_publish_resumed_context(clean_patch, monkeypatch):
    """The public Instrument API publishes each task's context before it resumes."""
    marker = ContextVar("marker", default=None)
    switches = []

    def dispatch(event):
        assert event == PYTHON_CONTEXT_SWITCH_EVENT
        switches.append(marker.get())

    async def child(name):
        marker.set(name)
        await trio.lowlevel.checkpoint()

    async def exercise():
        async with trio.open_nursery() as nursery:
            nursery.start_soon(child, "first")
            nursery.start_soon(child, "second")

    _install_fallback(monkeypatch, dispatch)
    trio.run(exercise)

    assert {"first", "second"}.issubset(switches)
    assert switches[-1] is None


def test_caller_instrument_is_preserved(clean_patch, monkeypatch):
    """Adding the context instrument does not replace caller instrumentation."""
    task_steps = []

    class CallerInstrument(trio.abc.Instrument):
        def before_task_step(self, task):
            task_steps.append(task.name)

    async def exercise():
        await trio.lowlevel.checkpoint()

    _install_fallback(monkeypatch, lambda event: None)
    trio.run(exercise, instruments=(CallerInstrument(),))

    assert task_steps


@pytest.mark.parametrize("fails", [False, True])
def test_worker_publishes_and_clears_context(clean_patch, monkeypatch, fails):
    """Trio owns publication for its public worker-thread entry point."""
    marker = ContextVar("marker", default=None)
    main_thread = threading.get_ident()
    switches = []

    def dispatch(event):
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

    _install_fallback(monkeypatch, dispatch)
    trio.run(exercise)

    assert switches == ["caller", None]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_from_thread_callback_publishes_and_clears_context(clean_patch, monkeypatch, asynchronous):
    """Both public from_thread entry points publish each callback's copied ContextVars."""
    marker = ContextVar("marker", default=None)
    main_thread = threading.get_ident()
    switches = []

    def dispatch(event):
        assert event == PYTHON_CONTEXT_SWITCH_EVENT
        if threading.get_ident() == main_thread:
            switches.append(marker.get())

    def sync_callback():
        assert marker.get() == "worker"
        return "done"

    async def async_callback():
        assert marker.get() == "worker"
        await trio.lowlevel.checkpoint()
        return "done"

    def worker():
        marker.set("worker")
        if asynchronous:
            return trio.from_thread.run(async_callback)
        return trio.from_thread.run_sync(sync_callback)

    async def exercise():
        assert await trio.to_thread.run_sync(worker) == "done"

    _install_fallback(monkeypatch, dispatch)
    trio.run(exercise)

    callback_index = switches.index("worker")
    assert None in switches[callback_index + 1 :]
