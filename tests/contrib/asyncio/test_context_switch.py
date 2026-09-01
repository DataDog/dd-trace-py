import asyncio
from contextvars import ContextVar
from contextvars import copy_context
import socket
import sys
import threading
from types import SimpleNamespace

import pytest
import wrapt

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


@pytest.fixture
def uvloop_dispatches(asyncio_patch_state, monkeypatch):
    """Record only calls to core.dispatch made by the uvloop wrapper."""
    marker = ContextVar("uvloop_context_switch_marker", default=None)
    dispatched_contexts = []

    def record_context_switch(_event):
        dispatched_contexts.append(marker.get())

    monkeypatch.setattr(_context_switch, "context_switches_require_fallback", lambda: True)
    monkeypatch.setattr(_context_switch_uvloop, "core", SimpleNamespace(dispatch=record_context_switch))
    patch()
    return marker, dispatched_contexts


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


def _run_uvloop(loop, timeout=5):
    """Run until a callback stops the loop, failing after a timeout instead of hanging CI."""
    overran = []

    def give_up():
        overran.append(True)
        loop.call_soon_threadsafe(loop.stop)

    # A thread rather than loop.call_later: scheduling from here would add a publication
    # to the ones these tests count, and would do it after the ambient marker is set.
    watchdog = threading.Timer(timeout, give_up)
    watchdog.start()
    try:
        loop.run_forever()
    finally:
        watchdog.cancel()

    assert not overran, "the loop was still running after %s seconds" % timeout


def _schedule(loop, entry_point, callback, context):
    """Queue a callback through one of uvloop's Python scheduling entry points."""
    if entry_point == "call_soon_threadsafe":
        # It has its own wrapper and runs on its own thread, so it is scheduled separately.
        scheduler = threading.Thread(target=loop.call_soon_threadsafe, args=(callback,), kwargs={"context": context})
        scheduler.start()
        scheduler.join()
    elif entry_point == "call_later":
        loop.call_later(0, callback, context=context)
    elif entry_point == "call_at":
        loop.call_at(loop.time(), callback, context=context)
    else:
        loop.call_soon(callback, context=context)


# Every Loop method the shim covers, listed here so that dropping one from _patch shows
# up as a coverage gap rather than as one less method to check.
_UVLOOP_WRAPPERS = (
    ("call_soon", _context_switch_uvloop._wrapped_call_soon),
    ("call_soon_threadsafe", _context_switch_uvloop._wrapped_call_soon_threadsafe),
    ("call_later", _context_switch_uvloop._wrapped_call_later),
    ("add_reader", _context_switch_uvloop._wrapped_add_reader),
    ("add_writer", _context_switch_uvloop._wrapped_add_writer),
    ("add_signal_handler", _context_switch_uvloop._wrapped_add_signal_handler),
    ("call_exception_handler", _context_switch_uvloop._wrapped_call_exception_handler),
    ("run_forever", _context_switch_uvloop._wrapped_run_forever),
)


def _wrapper_state(uvloop):
    """Which Loop methods currently carry this module's own wrapper."""
    return {
        method: getattr(getattr(uvloop.Loop, method, None), "_self_wrapper", None) is wrapper
        for method, wrapper in _UVLOOP_WRAPPERS
    }


# call_at is only covered because uvloop implements it by calling call_later. Keeping it
# here catches both a publication lost if that stops being true and a duplicate one if
# call_at is ever wrapped too.
@pytest.mark.parametrize("entry_point", ["call_soon", "call_soon_threadsafe", "call_later", "call_at"])
def test_uvloop_callbacks_publish_the_captured_context(uvloop_dispatches, entry_point):
    """Every scheduling entry point enters the callback context and restores the ambient one."""
    marker, dispatched_contexts = uvloop_dispatches
    loop = _new_uvloop_event_loop()
    try:
        marker.set("callback")
        callback_context = copy_context()
        # The ambient marker is set after scheduling on purpose: only a snapshot taken as
        # run_forever is entered can see it. One taken at schedule or patch time would
        # report "callback" here instead.
        _schedule(loop, entry_point, loop.stop, callback_context)
        marker.set("ambient")
        _run_uvloop(loop)

        assert dispatched_contexts == ["callback", "ambient", "ambient"]
    finally:
        loop.close()


def test_uvloop_reader_callbacks_publish_the_captured_context(uvloop_dispatches):
    """add_reader keeps the Context of its registration, not of the run that dispatches it."""
    marker, dispatched_contexts = uvloop_dispatches
    loop = _new_uvloop_event_loop()
    reader, writer = socket.socketpair()
    try:
        marker.set("callback")
        copy_context().run(loop.add_reader, reader.fileno(), loop.stop)
        marker.set("ambient")
        writer.send(b"!")
        _run_uvloop(loop)

        assert dispatched_contexts[:2] == ["callback", "ambient"]
        assert dispatched_contexts[-1] == "ambient"
    finally:
        loop.remove_reader(reader.fileno())
        loop.close()
        reader.close()
        writer.close()


def test_uvloop_callback_failure_reports_the_callback_context(uvloop_dispatches):
    """uvloop runs the exception handler in the failed callback's context, not the ambient one."""
    marker, dispatched_contexts = uvloop_dispatches
    loop = _new_uvloop_event_loop()
    handled = []

    def application_callback():
        raise RuntimeError("application failure")

    def exception_handler(_loop, _context):
        handled.append((marker.get(), dispatched_contexts[-1]))
        loop.stop()

    try:
        loop.set_exception_handler(exception_handler)
        marker.set("callback")
        callback_context = copy_context()
        loop.call_soon(application_callback, context=callback_context)
        marker.set("ambient")
        _run_uvloop(loop)

        # The handler runs in the failed callback's context, and that is what was published.
        assert handled == [("callback", "callback")]
        assert dispatched_contexts[-1] == "ambient"
    finally:
        loop.close()


def test_uvloop_uninstall_in_callback_still_restores_the_ambient_context(uvloop_dispatches):
    """Uninstalling during a callback must not leave an entry publication without its exit."""
    marker, dispatched_contexts = uvloop_dispatches
    loop = _new_uvloop_event_loop()
    observed = []
    try:
        marker.set("callback")
        callback_context = copy_context()

        def callback():
            observed.append(list(dispatched_contexts))
            unpatch()
            loop.stop()

        loop.call_soon(callback, context=callback_context)
        marker.set("ambient")
        _run_uvloop(loop)

        assert observed == [["callback"]]
        assert dispatched_contexts == ["callback", "ambient", "ambient"]
    finally:
        loop.close()


def test_uvloop_rejected_nested_run_publishes_nothing(uvloop_dispatches):
    """A refused re-entry publishes no switch and leaves the outer run's snapshot intact."""
    marker, dispatched_contexts = uvloop_dispatches
    loop = _new_uvloop_event_loop()
    rejected = []

    def nested_run():
        with pytest.raises(RuntimeError):
            loop.run_forever()
        rejected.append(True)

    try:
        marker.set("callback")
        callback_context = copy_context()
        loop.call_soon(nested_run, context=callback_context)
        loop.call_soon(loop.stop, context=callback_context)
        marker.set("ambient")
        _run_uvloop(loop)

        assert rejected == [True]
        assert dispatched_contexts == ["callback", "ambient", "callback", "ambient", "ambient"]
    finally:
        loop.close()


def test_uvloop_late_patch_skips_the_run_in_progress(asyncio_patch_state, monkeypatch):
    """Do not emit half a switch for a running loop; start at its next complete run."""
    marker = ContextVar("late_patch_marker", default=None)
    dispatched_contexts = []

    def record_context_switch(_event):
        dispatched_contexts.append(marker.get())

    async def main(loop):
        # This run started before patching, so it has no Context to restore to.
        marker.set("ambient")
        monkeypatch.setattr(_context_switch, "context_switches_require_fallback", lambda: True)
        monkeypatch.setattr(_context_switch_uvloop, "core", SimpleNamespace(dispatch=record_context_switch))
        patch()

        marker.set("callback")
        callback_context = copy_context()
        done = loop.create_future()
        loop.call_soon(done.set_result, None, context=callback_context)
        marker.set("ambient")
        await done

    loop = _new_uvloop_event_loop()
    try:
        # wait_for, not a bare run: this exercises the pass-through branch, where a
        # regression queues nothing at all and the loop would otherwise never return.
        loop.run_until_complete(asyncio.wait_for(main(loop), 5))
        assert dispatched_contexts == []

        marker.set("next_callback")
        callback_context = copy_context()
        loop.call_soon(loop.stop, context=callback_context)
        marker.set("next_ambient")
        _run_uvloop(loop)

        assert dispatched_contexts == ["next_callback", "next_ambient", "next_ambient"]
    finally:
        loop.close()


@pytest.mark.parametrize("fallback_required", [False, True])
def test_uvloop_scheduling_hooks_follow_install_state(asyncio_patch_state, monkeypatch, fallback_required):
    """Install uvloop wrappers only for fallback runtimes and remove them without residue."""
    uvloop = pytest.importorskip("uvloop")
    monkeypatch.setattr(_context_switch, "context_switches_require_fallback", lambda: fallback_required)
    unwrapped = {method: False for method, _ in _UVLOOP_WRAPPERS}
    installed = {method: fallback_required for method, _ in _UVLOOP_WRAPPERS}

    assert _wrapper_state(uvloop) == unwrapped

    patch()
    assert _wrapper_state(uvloop) == installed
    assert getattr(uvloop.Loop, _context_switch_uvloop._PATCH_MARKER, False) is fallback_required

    unpatch()
    assert _wrapper_state(uvloop) == unwrapped
    assert not hasattr(uvloop.Loop, _context_switch_uvloop._PATCH_MARKER)

    # Re-installing has to wrap again rather than assume the marker survived.
    patch()
    assert _wrapper_state(uvloop) == installed


def test_uvloop_patch_failure_spares_the_asyncio_integration(asyncio_patch_state, monkeypatch):
    """A uvloop that renamed a method loses its context switches, not the whole integration."""
    uvloop = pytest.importorskip("uvloop")
    monkeypatch.setattr(_context_switch, "context_switches_require_fallback", lambda: True)
    real_wrap = wrapt.wrap_function_wrapper

    def wrap_unless_uvloop_renamed_it(module, name, wrapper):
        if name == "Loop.run_forever":
            raise AttributeError("no such method, this uvloop calls it something else")
        return real_wrap(module, name, wrapper)

    monkeypatch.setattr(wrapt, "wrap_function_wrapper", wrap_unless_uvloop_renamed_it)

    patch()

    assert not any(_wrapper_state(uvloop).values())
    assert not hasattr(uvloop.Loop, _context_switch_uvloop._PATCH_MARKER)
    assert is_wrapped(asyncio.Handle._run)


def test_uvloop_unpatch_keeps_another_librarys_wrapper(asyncio_patch_state, monkeypatch):
    """Unwrapping whatever sits on top would remove another library's wrapper."""
    uvloop = pytest.importorskip("uvloop")
    monkeypatch.setattr(_context_switch, "context_switches_require_fallback", lambda: True)
    foreign_calls = []

    def foreign_wrapper(wrapped, _instance, args, kwargs):
        foreign_calls.append(args[0])
        return wrapped(*args, **kwargs)

    patch()
    wrapt.wrap_function_wrapper(uvloop, "Loop.call_soon", foreign_wrapper)
    loop = _new_uvloop_event_loop()
    try:
        unpatch()
        loop.call_soon(loop.stop)
        _run_uvloop(loop)

        assert foreign_calls == [loop.stop]
        # Ours stays underneath, so the marker is kept and a later patch must not re-wrap.
        assert getattr(uvloop.Loop, _context_switch_uvloop._PATCH_MARKER, False)
    finally:
        loop.close()
        uvloop.Loop.call_soon = uvloop.Loop.call_soon.__wrapped__
        _context_switch_uvloop._unpatch(uvloop)


@pytest.mark.parametrize("loop_factory", [asyncio.new_event_loop, _new_uvloop_event_loop], ids=["asyncio", "uvloop"])
def test_task_switches_publish_the_resumed_context(tracer, context_switches, loop_factory):
    """Both loop implementations expose each resumed child span before returning to ambient."""

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
