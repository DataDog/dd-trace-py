from contextvars import ContextVar
import threading
from types import SimpleNamespace

import anyio
import pytest
import trio

from ddtrace.contrib.internal.anyio import patch as anyio_patch
from ddtrace.contrib.internal.trio import patch as trio_patch
from ddtrace.internal._context_watcher import PYTHON_CONTEXT_SWITCH_EVENT


@pytest.fixture
def clean_patch(monkeypatch):
    """Restore the AnyIO and Trio patch states after a test changes the fallback gate."""
    was_anyio_patched = getattr(anyio, "_datadog_patch", False)
    was_trio_patched = getattr(trio, "_datadog_patch", False)
    anyio_patch.unpatch()
    trio_patch.unpatch()
    try:
        yield
    finally:
        monkeypatch.undo()
        anyio_patch.unpatch()
        trio_patch.unpatch()
        if was_anyio_patched:
            anyio_patch.patch()
        if was_trio_patched:
            trio_patch.patch()


@pytest.mark.parametrize("fails", [False, True])
def test_asyncio_worker_publishes_context(clean_patch, monkeypatch, fails):
    """AnyIO owns worker publication when its asyncio backend executes the callable."""
    marker = ContextVar("marker", default=None)
    switches = []

    def record_context_switch(event):
        assert event == PYTHON_CONTEXT_SWITCH_EVENT
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
                await anyio.to_thread.run_sync(worker)
        else:
            assert await anyio.to_thread.run_sync(worker) == "done"

    monkeypatch.setattr(anyio_patch, "context_switches_require_fallback", lambda: True)
    monkeypatch.setattr(
        anyio_patch, "core", SimpleNamespace(dispatch=record_context_switch, has_listeners=lambda event: True)
    )
    anyio_patch.patch()
    anyio.run(exercise, backend="asyncio")

    assert switches == ["caller", None]


def test_trio_backend_delegates_worker_publication(clean_patch, monkeypatch):
    """The AnyIO facade leaves the actual Trio worker boundary to Trio."""
    marker = ContextVar("marker", default=None)
    main_thread = threading.get_ident()
    switches = []

    def record_context_switch(event):
        assert event == PYTHON_CONTEXT_SWITCH_EVENT
        if threading.get_ident() != main_thread:
            switches.append(marker.get())

    async def exercise():
        marker.set("caller")
        await anyio.to_thread.run_sync(lambda: None)

    recorder = SimpleNamespace(dispatch=record_context_switch, has_listeners=lambda event: True)
    monkeypatch.setattr(anyio_patch, "context_switches_require_fallback", lambda: True)
    monkeypatch.setattr(trio_patch, "context_switches_require_fallback", lambda: True)
    monkeypatch.setattr(anyio_patch, "core", recorder)
    monkeypatch.setattr(trio_patch, "core", recorder)
    anyio_patch.patch()
    trio_patch.patch()
    anyio.run(exercise, backend="trio")

    assert switches == ["caller", None]
