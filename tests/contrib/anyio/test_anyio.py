from contextvars import ContextVar

import anyio
import pytest

from ddtrace.contrib.internal.anyio import patch as anyio_patch
from ddtrace.internal import core
from ddtrace.internal._context_watcher import PYTHON_CONTEXT_SWITCH_EVENT
from ddtrace.internal.wrapping import is_wrapped_with


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


@pytest.mark.parametrize("fallback_required", [False, True])
def test_patch_follows_runtime_capability(clean_patch, monkeypatch, fallback_required):
    """Install the worker wrapper only when native context watching cannot be used."""
    monkeypatch.setattr(anyio_patch, "context_switches_require_fallback", lambda: fallback_required)

    anyio_patch.patch()
    anyio_patch.patch()

    assert is_wrapped_with(anyio.to_thread.run_sync, anyio_patch._wrapped_run_sync) is fallback_required

    anyio_patch.unpatch()
    anyio_patch.unpatch()
    assert not is_wrapped_with(anyio.to_thread.run_sync, anyio_patch._wrapped_run_sync)


@pytest.mark.parametrize("backend", ["asyncio", "trio"])
@pytest.mark.parametrize("fails", [False, True])
def test_run_sync_publishes_worker_context(clean_patch, monkeypatch, backend, fails):
    """Publish entry and exit for successful and failing workers on each backend."""
    marker = ContextVar("marker", default=None)
    switches = []

    def record_context_switch():
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
    core.on(PYTHON_CONTEXT_SWITCH_EVENT, record_context_switch)
    anyio_patch.patch()
    try:
        anyio.run(exercise, backend=backend)
    finally:
        core.reset_listeners(PYTHON_CONTEXT_SWITCH_EVENT, record_context_switch)

    assert switches == ["caller", None]
