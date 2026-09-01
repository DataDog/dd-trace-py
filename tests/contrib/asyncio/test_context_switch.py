import asyncio
from contextvars import ContextVar
from contextvars import copy_context
import sys
import threading
from types import SimpleNamespace

import pytest

from ddtrace.contrib.internal.asyncio import _context_switch
from ddtrace.contrib.internal.asyncio import _context_switch_uvloop
from ddtrace.contrib.internal.asyncio.patch import _wrapped_create_task as _wrapped_trace_create_task
from ddtrace.contrib.internal.asyncio.patch import patch
from ddtrace.contrib.internal.asyncio.patch import unpatch
from ddtrace.internal import core
from ddtrace.internal._context_watcher import PYTHON_CONTEXT_SWITCH_EVENT
from ddtrace.internal.wrapping import is_wrapped
from ddtrace.internal.wrapping import is_wrapped_with


def _new_uvloop_event_loop():
    uvloop = pytest.importorskip("uvloop")

    return uvloop.new_event_loop()


@pytest.fixture
def asyncio_patch_state():
    was_patched = getattr(asyncio, "_datadog_patch", False)
    unpatch()
    try:
        yield
    finally:
        unpatch()
        if was_patched:
            patch()


@pytest.fixture
def context_switches(tracer, asyncio_patch_state):
    switches = []

    def record_context_switch():
        switches.append(tracer.context_provider.active())

    core.on(PYTHON_CONTEXT_SWITCH_EVENT, record_context_switch)
    try:
        patch()
        if not _context_switch.context_switches_require_fallback():
            pytest.skip("the native context watcher is active")
        yield switches
    finally:
        core.reset_listeners(PYTHON_CONTEXT_SWITCH_EVENT, record_context_switch)


@pytest.mark.parametrize("fallback_required", [False, True])
def test_context_switch_hooks_follow_runtime_capability(fallback_required, asyncio_patch_state, monkeypatch):
    """Install fallback hooks only when needed, avoiding overlap with the native watcher."""
    monkeypatch.setattr(_context_switch, "context_switches_require_fallback", lambda: fallback_required)
    eager_task_factory = getattr(asyncio, "eager_task_factory", None)

    patch()

    eager_fallback = fallback_required and _context_switch._eager_task_factory_code is not None
    exception_handler_fallback = fallback_required and sys.version_info < (3, 12)
    assert is_wrapped_with(asyncio.BaseEventLoop.create_task, _wrapped_trace_create_task)
    assert is_wrapped_with(asyncio.BaseEventLoop.create_task, _context_switch._wrapped_create_task) is eager_fallback
    assert is_wrapped_with(asyncio.Handle._run, _context_switch._wrapped_run_handle) is fallback_required
    assert (
        is_wrapped_with(asyncio.BaseEventLoop.call_exception_handler, _context_switch._wrapped_call_exception_handler)
        is exception_handler_fallback
    )
    if eager_task_factory is not None:
        assert asyncio.eager_task_factory is eager_task_factory
        assert (
            is_wrapped_with(asyncio.eager_task_factory, _context_switch._wrapped_eager_task_factory) is eager_fallback
        )
    assert not is_wrapped(asyncio.to_thread)

    unpatch()
    assert not is_wrapped(asyncio.BaseEventLoop.create_task)
    assert not is_wrapped(asyncio.Handle._run)
    assert not is_wrapped(asyncio.BaseEventLoop.call_exception_handler)
    if eager_task_factory is not None:
        assert asyncio.eager_task_factory is eager_task_factory
        assert not is_wrapped(asyncio.eager_task_factory)


@pytest.mark.subprocess(
    env={"DD_TRACE_OTEL_CTX_ENABLED": "false"},
    parametrize={
        "TASK_MODE": [
            "lazy",
            pytest.param(
                "eager_factory",
                marks=pytest.mark.skipif(sys.version_info < (3, 12), reason="eager tasks require Python 3.12+"),
            ),
            pytest.param(
                "direct_eager_factory",
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
def test_task_creation_publishes_only_inline_context_switches():
    """Leave lazy tasks untouched and publish one switch pair for eager task construction."""
    import asyncio
    from contextvars import ContextVar
    from contextvars import copy_context
    import os

    from ddtrace.contrib.internal.asyncio.patch import patch
    from ddtrace.contrib.internal.asyncio.patch import unpatch
    from ddtrace.internal import core
    from ddtrace.internal._context_watcher import PYTHON_CONTEXT_SWITCH_EVENT

    mode = os.environ["TASK_MODE"]
    eager_task_factory = getattr(asyncio, "eager_task_factory", None)
    marker = ContextVar("marker", default=None)
    switches = []

    def record_context_switch():
        switches.append(marker.get())

    async def child():
        return "done"

    async def main():
        loop = asyncio.get_running_loop()
        original_factory = loop.get_task_factory()
        marker.set("task")
        task_context = copy_context()
        marker.set("caller")

        try:
            switches.clear()
            if mode == "lazy":
                task = loop.create_task(child())
            elif mode == "eager_factory":
                assert eager_task_factory is not None
                loop.set_task_factory(eager_task_factory)
                task = loop.create_task(child(), context=task_context)
            elif mode == "direct_eager_factory":
                assert eager_task_factory is not None
                task = eager_task_factory(loop, child(), context=task_context)
            else:
                task = loop.create_task(child(), context=task_context, eager_start=True)

            assert switches == ([] if mode == "lazy" else ["task", "caller"])
            assert await task == "done"
        finally:
            loop.set_task_factory(original_factory)

    core.on(PYTHON_CONTEXT_SWITCH_EVENT, record_context_switch)
    unpatch()
    patch()

    try:
        asyncio.run(main())
    finally:
        unpatch()
        core.reset_listeners(PYTHON_CONTEXT_SWITCH_EVENT, record_context_switch)


@pytest.mark.asyncio
@pytest.mark.skipif(sys.version_info < (3, 12), reason="eager tasks require Python 3.12+")
async def test_eager_task_factory_rejects_non_coroutines_synchronously(asyncio_patch_state, monkeypatch):
    """Preserve asyncio's synchronous input validation while the eager fallback is active."""
    monkeypatch.setattr(_context_switch, "context_switches_require_fallback", lambda: True)
    patch()

    loop = asyncio.get_running_loop()
    original_factory = loop.get_task_factory()
    loop.set_task_factory(asyncio.eager_task_factory)
    try:
        with pytest.raises(TypeError):
            loop.create_task(1)
    finally:
        loop.set_task_factory(original_factory)


def test_callback_failure_reports_the_application_callback(context_switches):
    """Keep asyncio error reports attributed to the application despite the temporary callback wrapper."""

    def application_callback():
        raise RuntimeError("application failure")

    loop = asyncio.new_event_loop()
    handled = []
    loop.set_exception_handler(lambda _loop, context: handled.append(context))
    try:
        handle = asyncio.Handle(application_callback, (), loop, context=copy_context())
        handle._run()
    finally:
        loop.close()

    assert len(handled) == 1
    assert handled[0]["exception"].args == ("application failure",)
    assert "application_callback" in handled[0]["message"]
    assert handle._callback is application_callback


@pytest.mark.skipif(sys.version_info >= (3, 12), reason="exception handlers inherit the originating context on 3.12+")
def test_callback_failure_resynchronizes_before_exception_handler(context_switches):
    """Publish the ambient context before a pre-3.12 exception handler runs."""
    marker = ContextVar("marker", default="ambient")
    published = []
    handled = []

    def record_context_switch():
        published.append(marker.get())

    def application_callback():
        raise RuntimeError("application failure")

    token = marker.set("callback")
    callback_context = copy_context()
    marker.reset(token)

    loop = asyncio.new_event_loop()
    loop.set_exception_handler(lambda _loop, _context: handled.append((marker.get(), published[-1])))
    core.on(PYTHON_CONTEXT_SWITCH_EVENT, record_context_switch)
    try:
        asyncio.Handle(application_callback, (), loop, context=callback_context)._run()
    finally:
        core.reset_listeners(PYTHON_CONTEXT_SWITCH_EVENT, record_context_switch)
        loop.close()

    assert handled == [("ambient", "ambient")]


# call_at is only covered because uvloop implements it by calling call_later. Keep it
# parametrized: it catches both a lost publication if uvloop stops delegating and a
# duplicate one if call_at is ever wrapped too.
@pytest.mark.parametrize("schedule_method", ["call_later", "call_at"])
def test_uvloop_timer_callbacks_publish_the_captured_context(asyncio_patch_state, monkeypatch, schedule_method):
    """Publish timer callback entry and restore the loop's ambient context."""
    monkeypatch.setattr(_context_switch, "context_switches_require_fallback", lambda: True)
    marker = ContextVar("timer_callback_marker", default=None)
    switches = []

    def record_context_switch(_event):
        switches.append(marker.get())

    monkeypatch.setattr(_context_switch_uvloop, "core", SimpleNamespace(dispatch=record_context_switch))
    patch()
    loop = _new_uvloop_event_loop()
    try:
        marker.set("callback")
        callback_context = copy_context()
        observed = []

        def callback():
            observed.append((marker.get(), switches[-1] if switches else None))
            loop.stop()

        if schedule_method == "call_later":
            loop.call_later(0.05, callback, context=callback_context)
        else:
            loop.call_at(loop.time() + 0.05, callback, context=callback_context)
        # Set after scheduling, so only a snapshot taken as run_forever is entered sees it.
        # A capture at schedule or patch time would report "callback" instead.
        marker.set("ambient")
        loop.run_forever()

        assert observed == [("callback", "callback")]
        # Callback entry, its restore, then the restore the run_forever wrapper publishes
        # as the loop exits.
        assert switches == ["callback", "ambient", "ambient"]
    finally:
        loop.close()


def test_uvloop_uninstall_in_callback_stops_publication(asyncio_patch_state, monkeypatch):
    """Do not publish callback restoration or loop exit after uninstalling in a callback."""
    monkeypatch.setattr(_context_switch, "context_switches_require_fallback", lambda: True)
    marker = ContextVar("uninstall_callback_marker", default=None)
    switches = []

    def record_context_switch(_event):
        switches.append(marker.get())

    monkeypatch.setattr(_context_switch_uvloop, "core", SimpleNamespace(dispatch=record_context_switch))
    patch()
    loop = _new_uvloop_event_loop()
    try:
        marker.set("callback")
        callback_context = copy_context()

        def callback():
            assert switches == ["callback"]
            unpatch()
            loop.stop()

        loop.call_soon(callback, context=callback_context)
        marker.set("ambient")
        loop.run_forever()

        assert switches == ["callback"]
    finally:
        loop.close()


def test_uvloop_nested_run_keeps_publishing_the_ambient_context(asyncio_patch_state, monkeypatch):
    """Keep the ambient context of a running loop when a nested run is rejected."""
    monkeypatch.setattr(_context_switch, "context_switches_require_fallback", lambda: True)
    marker = ContextVar("nested_run_marker", default=None)
    switches = []

    def record_context_switch(_event):
        switches.append(marker.get())

    monkeypatch.setattr(_context_switch_uvloop, "core", SimpleNamespace(dispatch=record_context_switch))
    patch()
    loop = _new_uvloop_event_loop()

    def nested_run():
        # A sync bridge reaching back into the loop gets a RuntimeError it can handle. That
        # must not cost the outer run the ambient context snapshot it still needs.
        with pytest.raises(RuntimeError):
            loop.run_forever()

    def schedule_probe():
        switches.clear()
        loop.call_soon(callback, context=callback_context)

    observed = []

    def callback():
        observed.append(marker.get())
        loop.stop()

    try:
        marker.set("callback")
        callback_context = copy_context()
        loop.call_soon(nested_run)
        loop.call_soon(schedule_probe)
        marker.set("ambient")
        loop.run_forever()

        assert observed == ["callback"]
        # The middle entry is the restore that the rejected nested run used to suppress.
        assert switches[-3:] == ["callback", "ambient", "ambient"]
    finally:
        loop.close()


def test_uvloop_late_patch_skips_current_run(asyncio_patch_state, monkeypatch):
    """Skip an active run and begin instrumentation on the next one."""
    monkeypatch.setattr(_context_switch, "context_switches_require_fallback", lambda: True)
    marker = ContextVar("late_patch_marker", default=None)
    switches = []

    def record_context_switch(_event):
        switches.append(marker.get())

    async def main(loop):
        # run_forever is already in flight and unwrapped, so this run has no known ambient
        # context. It must not emit partial, incorrectly restored context-switch events.
        marker.set("ambient")
        patch()
        assert not hasattr(loop, _context_switch_uvloop._AMBIENT_CONTEXT_ATTR)
        switches.clear()

        marker.set("callback")
        callback_context = copy_context()
        marker.set("ambient")
        done = loop.create_future()
        observed = []

        def callback():
            observed.append((marker.get(), switches[-1] if switches else None))
            done.set_result(None)

        loop.call_soon(callback, context=callback_context)
        await done

        assert observed == [("callback", None)]
        assert switches == []

    monkeypatch.setattr(_context_switch_uvloop, "core", SimpleNamespace(dispatch=record_context_switch))
    loop = _new_uvloop_event_loop()
    try:
        loop.run_until_complete(main(loop))

        marker.set("next_callback")
        callback_context = copy_context()
        marker.set("next_ambient")

        def callback():
            loop.stop()

        loop.call_soon(callback, context=callback_context)
        loop.run_forever()

        assert switches == ["next_callback", "next_ambient", "next_ambient"]
    finally:
        loop.close()


def test_uvloop_threadsafe_callbacks_publish_the_captured_context(asyncio_patch_state, monkeypatch):
    """Publish entry and restore for a callback scheduled from another thread."""
    monkeypatch.setattr(_context_switch, "context_switches_require_fallback", lambda: True)
    marker = ContextVar("threadsafe_marker", default=None)
    switches = []

    def record_context_switch(_event):
        switches.append(marker.get())

    monkeypatch.setattr(_context_switch_uvloop, "core", SimpleNamespace(dispatch=record_context_switch))
    patch()
    loop = _new_uvloop_event_loop()
    try:
        marker.set("callback")
        callback_context = copy_context()
        observed = []

        def callback():
            observed.append(marker.get())
            loop.stop()

        # call_soon_threadsafe has its own wrapper, so it needs its own coverage.
        scheduler = threading.Thread(target=lambda: loop.call_soon_threadsafe(callback, context=callback_context))
        scheduler.start()
        scheduler.join()
        marker.set("ambient")
        loop.run_forever()

        assert observed == ["callback"]
        assert switches == ["callback", "ambient", "ambient"]
    finally:
        loop.close()


@pytest.mark.parametrize("fallback_required", [False, True])
def test_uvloop_scheduling_hooks_follow_install_state(asyncio_patch_state, monkeypatch, fallback_required):
    """Wrap the uvloop scheduling methods only while the fallback is installed."""
    uvloop = pytest.importorskip("uvloop")
    monkeypatch.setattr(_context_switch, "context_switches_require_fallback", lambda: fallback_required)
    wrapped_methods = ("call_soon", "call_soon_threadsafe", "call_later", "run_forever")

    def wrapped_state():
        return [hasattr(getattr(uvloop.Loop, name), "__wrapped__") for name in wrapped_methods]

    unwrapped = [False] * len(wrapped_methods)
    installed = [fallback_required] * len(wrapped_methods)

    assert wrapped_state() == unwrapped

    patch()
    assert wrapped_state() == installed
    assert getattr(uvloop.Loop, _context_switch_uvloop._PATCH_MARKER, False) is fallback_required

    unpatch()
    assert wrapped_state() == unwrapped
    assert not hasattr(uvloop.Loop, _context_switch_uvloop._PATCH_MARKER)

    # Re-installing has to wrap again rather than assume the marker survived.
    patch()
    assert wrapped_state() == installed


def test_uvloop_callbacks_publish_the_active_trace_context(tracer, context_switches):
    """Publish the callback's own trace context through the real listener chain."""
    loop = _new_uvloop_event_loop()
    try:
        with tracer.trace("ambient") as ambient:
            # active() ignores finished spans, so keep this one open and put the ambient
            # span back as the active one for the scheduling side.
            callback_span = tracer.trace("callback")
            callback_context = copy_context()
            tracer.context_provider.activate(ambient)

            observed = []

            def callback():
                observed.append(tracer.context_provider.active())
                loop.stop()

            loop.call_later(0.01, callback, context=callback_context)
            loop.run_forever()
            callback_span.finish()

            assert observed == [callback_span]
            assert context_switches[-1] is ambient
    finally:
        loop.close()


@pytest.mark.parametrize("loop_factory", [asyncio.new_event_loop, _new_uvloop_event_loop], ids=["asyncio", "uvloop"])
def test_task_switches_publish_the_resumed_context(tracer, context_switches, loop_factory):
    """Publish each resumed task's context and detach it before another task runs."""

    async def child(name):
        with tracer.trace(name) as span:
            await asyncio.sleep(0)
            assert context_switches[-1] is span

    async def main():
        await asyncio.gather(child("first"), child("second"))

    loop = loop_factory()
    try:
        with tracer.trace("ambient") as ambient:
            loop.run_until_complete(main())
            assert context_switches[-1] is ambient
    finally:
        loop.close()
