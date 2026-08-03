import sys
import threading

import anyio
import pytest

import ddtrace
from ddtrace.contrib.internal.anyio.patch import patch
from ddtrace.contrib.internal.anyio.patch import unpatch
from ddtrace.internal import core
from ddtrace.internal.wrapping import is_wrapped
from ddtrace.trace import tracer


_CONTEXT_WATCHER_AVAILABLE = sys.implementation.name == "cpython" and sys.version_info >= (3, 14)


@pytest.fixture
def patched_anyio():
    was_patched = getattr(anyio, "_datadog_patch", False)
    patch()
    yield
    if not was_patched:
        unpatch()


def test_patch_and_unpatch():
    was_patched = getattr(anyio, "_datadog_patch", False)
    if was_patched:
        unpatch()

    try:
        ddtrace.patch(anyio=True)
        assert is_wrapped(anyio.to_thread.run_sync) is not _CONTEXT_WATCHER_AVAILABLE
        unpatch()
        assert not is_wrapped(anyio.to_thread.run_sync)
    finally:
        if was_patched:
            patch()


@pytest.mark.parametrize("raises", [False, True])
@pytest.mark.skipif(_CONTEXT_WATCHER_AVAILABLE, reason="CPython 3.14+ uses the native context watcher")
def test_context_switch_events_follow_worker_execution(patched_anyio, raises):
    switches = []
    worker_id = None

    def record_context_switch():
        switches.append((threading.get_ident(), tracer.context_provider.active()))

    def sync_listener():
        nonlocal worker_id
        worker_id = threading.get_ident()
        if raises:
            raise RuntimeError("listener failed")

    async def run_listener():
        with tracer.trace("parent") as parent:
            if raises:
                with pytest.raises(RuntimeError, match="listener failed"):
                    await anyio.to_thread.run_sync(sync_listener)
            else:
                await anyio.to_thread.run_sync(sync_listener)
            return parent

    core.on("python.context.switch", record_context_switch)
    try:
        parent = anyio.run(run_listener)
    finally:
        core.reset_listeners("python.context.switch", record_context_switch)

    assert worker_id is not None
    assert [context for ident, context in switches if ident == worker_id] == [parent, None]
