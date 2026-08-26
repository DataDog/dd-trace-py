import asyncio
from contextvars import copy_context
import sys

import pytest

from ddtrace.contrib.internal.asyncio import _context_switch
from ddtrace.contrib.internal.asyncio.patch import _wrapped_create_task as _wrapped_trace_create_task
from ddtrace.contrib.internal.asyncio.patch import patch
from ddtrace.contrib.internal.asyncio.patch import unpatch
from ddtrace.internal import core
from ddtrace.internal._context_watcher import PYTHON_CONTEXT_SWITCH_EVENT
from ddtrace.internal.wrapping import is_wrapped
from ddtrace.internal.wrapping import is_wrapped_with


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
    original_task = asyncio.Task

    patch()

    eager_fallback = fallback_required and _context_switch._eager_task_factory_code is not None
    assert is_wrapped_with(asyncio.BaseEventLoop.create_task, _wrapped_trace_create_task)
    assert is_wrapped_with(asyncio.BaseEventLoop.create_task, _context_switch._wrapped_create_task) is eager_fallback
    assert is_wrapped_with(asyncio.Handle._run, _context_switch._wrapped_run_handle) is fallback_required
    assert not is_wrapped(asyncio.to_thread)
    assert (asyncio.Task is not original_task) is eager_fallback

    unpatch()
    assert not is_wrapped(asyncio.BaseEventLoop.create_task)
    assert not is_wrapped(asyncio.Handle._run)
    assert asyncio.Task is original_task


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
                "direct_eager",
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
    """Leave lazy tasks untouched and publish exactly one switch pair for every eager entry path."""
    import asyncio
    from contextvars import copy_context
    import os

    from ddtrace.contrib.internal.asyncio.patch import patch
    from ddtrace.contrib.internal.asyncio.patch import unpatch
    from ddtrace.internal import core
    from ddtrace.internal._context_watcher import PYTHON_CONTEXT_SWITCH_EVENT
    from ddtrace.trace import tracer

    mode = os.environ["TASK_MODE"]
    switches = []

    def record_context_switch():
        switches.append(tracer.context_provider.active())

    async def child():
        return "done"

    async def main():
        loop = asyncio.get_running_loop()
        original_factory = loop.get_task_factory()
        caller = tracer.trace("caller")
        task_span = tracer.trace("task")
        task_context = copy_context()
        tracer.context_provider.activate(None if mode == "lazy" else caller)

        try:
            switches.clear()
            if mode == "lazy":
                received = []

                def lazy_factory(loop, coro, **kwargs):
                    received.append(coro)
                    return asyncio.Task(coro, loop=loop, **kwargs)

                loop.set_task_factory(lazy_factory)
                coro = child()
                task = loop.create_task(coro)
                assert received == [coro]
            elif mode == "eager_factory":
                loop.set_task_factory(asyncio.create_eager_task_factory(CustomTask))
                task = loop.create_task(child(), context=task_context)
                assert type(task) is CustomTask
            elif mode == "direct_eager":
                task = CustomTask(child(), loop=loop, context=task_context, eager_start=True, marker="direct")
                assert task.marker == "direct"
                assert isinstance(asyncio.current_task(), asyncio.Task)
            else:
                task = loop.create_task(child(), context=task_context, eager_start=True)

            assert switches == ([] if mode == "lazy" else [task_span, caller])
            assert await task == "done"
        finally:
            loop.set_task_factory(original_factory)
            tracer.context_provider.activate(None)
            task_span.finish()
            caller.finish()

    core.on(PYTHON_CONTEXT_SWITCH_EVENT, record_context_switch)
    unpatch()
    patch()

    class CustomTask(asyncio.Task):
        def __init__(self, coro, *, marker=None, **kwargs):
            self.marker = marker
            super().__init__(coro, **kwargs)

    try:
        asyncio.run(main())
    finally:
        unpatch()
        core.reset_listeners(PYTHON_CONTEXT_SWITCH_EVENT, record_context_switch)


@pytest.mark.asyncio
@pytest.mark.skipif(sys.version_info < (3, 12), reason="eager tasks require Python 3.12+")
@pytest.mark.parametrize("traced", [False, True])
async def test_eager_task_factory_rejects_non_coroutines_synchronously(
    tracer, asyncio_patch_state, monkeypatch, traced
):
    """Preserve asyncio's synchronous input validation while the eager fallback is active."""
    monkeypatch.setattr(_context_switch, "context_switches_require_fallback", lambda: True)
    patch()

    loop = asyncio.get_running_loop()
    original_factory = loop.get_task_factory()
    loop.set_task_factory(asyncio.eager_task_factory)
    span = tracer.trace("parent") if traced else None
    try:
        with pytest.raises(TypeError):
            loop.create_task(1)
    finally:
        loop.set_task_factory(original_factory)
        tracer.context_provider.activate(None)
        if span is not None:
            span.finish()


@pytest.mark.skipif(sys.version_info < (3, 12), reason="eager tasks require Python 3.12+")
def test_task_replacement_preserves_another_owner(asyncio_patch_state, monkeypatch):
    """Do not overwrite a Task class already replaced by another integration."""
    original_task = asyncio.Task

    class ForeignTask(original_task):
        pass

    monkeypatch.setattr(asyncio, "Task", ForeignTask)
    monkeypatch.setattr(_context_switch, "context_switches_require_fallback", lambda: True)
    try:
        _context_switch.install()
        assert asyncio.Task is ForeignTask
    finally:
        _context_switch.uninstall()

    assert asyncio.Task is ForeignTask


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


@pytest.mark.asyncio
async def test_task_switches_publish_the_resumed_context(tracer, context_switches):
    """Publish each resumed task's context and detach it before another task runs."""

    async def child(name):
        with tracer.trace(name) as span:
            await asyncio.sleep(0)
            assert context_switches[-1] is span

    await asyncio.gather(child("first"), child("second"))
    assert None in context_switches
