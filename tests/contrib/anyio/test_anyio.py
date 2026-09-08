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
    """Restore the AnyIO patch state after a test changes the fallback gate."""
    was_patched = getattr(anyio, "_datadog_patch", False)
    original_gate = anyio_patch.context_switches_require_fallback
    anyio_patch.unpatch()
    try:
        yield
    finally:
        anyio_patch.unpatch()
        monkeypatch.setattr(anyio_patch, "context_switches_require_fallback", original_gate)
        if was_patched:
            anyio_patch.patch()


@pytest.mark.parametrize("backend", ["asyncio", "trio"])
@pytest.mark.parametrize("fails", [False, True])
def test_run_sync_publishes_worker_context(clean_patch, monkeypatch, backend, fails):
    """Publish entry and exit for successful and failing workers on each backend."""
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
                await anyio.to_thread.run_sync(func=worker)
        else:
            assert await anyio.to_thread.run_sync(func=worker) == "done"

    monkeypatch.setattr(anyio_patch, "context_switches_require_fallback", lambda: True)
    monkeypatch.setattr(
        anyio_patch,
        "core",
        SimpleNamespace(dispatch=record_context_switch),
    )
    anyio_patch.patch()
    anyio.run(exercise, backend=backend)

    assert switches == ["caller", None]


def test_trio_backend_does_not_double_publish_worker_context(clean_patch, monkeypatch):
    """AnyIO and Trio cooperate when both integrations wrap the worker boundary."""
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

    was_trio_patched = getattr(trio, "_datadog_patch", False)
    trio_patch.unpatch()
    monkeypatch.setattr(anyio_patch, "context_switches_require_fallback", lambda: True)
    monkeypatch.setattr(trio_patch, "context_switches_require_fallback", lambda: True)
    monkeypatch.setattr(anyio_patch, "core", SimpleNamespace(dispatch=record_context_switch))
    monkeypatch.setattr(trio_patch, "core", SimpleNamespace(dispatch=record_context_switch))
    anyio_patch.patch()
    trio_patch.patch()
    try:
        anyio.run(exercise, backend="trio")
    finally:
        trio_patch.unpatch()
        if was_trio_patched:
            trio_patch.patch()

    assert switches == ["caller", None]
