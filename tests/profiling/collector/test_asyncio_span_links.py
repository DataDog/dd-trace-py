from __future__ import annotations

from unittest import mock

import pytest

from ddtrace.internal.datadog.profiling import stack
from ddtrace.profiling import _asyncio as profiling_asyncio


pytestmark = pytest.mark.skipif(not stack.is_available, reason="stack profiler not available")


def test_current_task_provider_requires_native_loop_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Require native loop registration before routing an activation to a task mapping."""
    task = mock.Mock()
    monkeypatch.setattr(profiling_asyncio, "get_running_loop", lambda: mock.sentinel.loop)
    monkeypatch.setattr(profiling_asyncio, "current_task", lambda: task)
    monkeypatch.setattr(profiling_asyncio, "_ensure_task_span_finalizer", lambda candidate: candidate is task)
    monkeypatch.setattr(profiling_asyncio.stack, "is_asyncio_loop_registered", lambda thread_id: False)

    assert profiling_asyncio._current_task_span_id() is None

    monkeypatch.setattr(profiling_asyncio.stack, "is_asyncio_loop_registered", lambda thread_id: True)
    assert profiling_asyncio._current_task_span_id() == id(task)


def test_completed_task_clears_mapping_before_object_finalization(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove task attribution promptly on completion and outside the task's inherited Context."""
    cleared = []
    monkeypatch.setattr(profiling_asyncio, "_clear_native_task_span", lambda task_id: cleared.append(task_id))

    class Task:
        callback = None

        def add_done_callback(self, callback, *, context):
            self.callback = callback
            self.context = context

    task = Task()
    assert profiling_asyncio._ensure_task_span_finalizer(task)
    assert task.callback is not None
    assert not list(task.context)

    task.context.run(task.callback, task)

    assert cleared == [id(task)]
    assert id(task) not in profiling_asyncio._task_span_finalizers


@pytest.mark.subprocess()
def test_nested_creation_wrappers_publish_task_only_once() -> None:
    """Publish one task mapping when nested asyncio creation hooks observe the same task."""
    import asyncio as aio
    from unittest import mock

    from ddtrace.profiling import _asyncio as profiling_asyncio

    async def main():
        task = aio.current_task()
        assert task is not None
        with mock.patch.object(profiling_asyncio._span_links, "link_task_span_context", return_value=True) as publish:
            profiling_asyncio._publish_task_span(task, None, False)
            profiling_asyncio._publish_task_span(task, None, False)
        publish.assert_called_once()

    aio.run(main())


def test_fork_reset_detaches_finalizers() -> None:
    """Discard parent-owned task finalizers in the forked child."""

    class Task:
        def add_done_callback(self, callback, *, context):
            self.callback = callback

    task = Task()
    assert profiling_asyncio._ensure_task_span_finalizer(task)

    profiling_asyncio._reset_task_span_state_after_fork()

    assert not profiling_asyncio._task_span_finalizers


@pytest.mark.subprocess()
def test_custom_factory_cancellation_closes_original_coroutine() -> None:
    import asyncio

    from ddtrace.profiling import _span_links

    _span_links.start_span_linking()

    async def child():
        raise AssertionError("cancelled coroutine must not execute")

    async def main():
        loop = asyncio.get_running_loop()
        loop.set_task_factory(lambda loop, coro, **kwargs: asyncio.Task(coro, loop=loop, **kwargs))
        coro = child()
        try:
            task = asyncio.create_task(coro)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            assert coro.cr_frame is None
        finally:
            loop.set_task_factory(None)
            coro.close()

    asyncio.run(main())


@pytest.mark.subprocess(parametrize={"FACTORY_CLOSES": ["0", "1"]})
def test_custom_factory_failure_preserves_coroutine_ownership() -> None:
    import asyncio
    import os

    from ddtrace.profiling import _span_links

    _span_links.start_span_linking()
    factory_closes = os.environ["FACTORY_CLOSES"] == "1"

    async def child():
        return 42

    async def main():
        def factory(loop, coro, **kwargs):
            if factory_closes:
                coro.close()
            raise ValueError("factory failed")

        loop = asyncio.get_running_loop()
        loop.set_task_factory(factory)
        coro = child()
        try:
            try:
                asyncio.create_task(coro)
            except ValueError as error:
                assert str(error) == "factory failed"
            else:
                raise AssertionError("factory failure was swallowed")
            assert (coro.cr_frame is None) == factory_closes
        finally:
            loop.set_task_factory(None)
            coro.close()

    asyncio.run(main())


@pytest.mark.subprocess()
def test_inactive_linking_does_not_wrap_custom_factory_coroutine() -> None:
    import asyncio

    from ddtrace.profiling import _span_links

    _span_links.stop_span_linking()

    async def child():
        return 42

    async def main():
        loop = asyncio.get_running_loop()
        loop.set_task_factory(lambda loop, coro, **kwargs: asyncio.Task(coro, loop=loop, **kwargs))
        coro = child()
        try:
            task = asyncio.create_task(coro)
            assert task.get_coro() is coro
            assert await task == 42
        finally:
            loop.set_task_factory(None)

    asyncio.run(main())


@pytest.mark.subprocess()
def test_task_publication_rolls_back_when_finalizer_allocation_fails() -> None:
    import asyncio
    from unittest import mock

    from ddtrace.profiling import _asyncio as profiling_asyncio
    from ddtrace.profiling import _span_links

    async def child():
        return 42

    async def main():
        _span_links.start_span_linking()
        try:
            _span_links.link_span(_span_links._SpanInfo(123, 123, None), None)
            # Bypass creation hooks so this task inherits metadata but has no cleanup installed yet.
            task = asyncio.Task(child())
            with (
                mock.patch.object(profiling_asyncio.weakref, "finalize", side_effect=MemoryError) as finalize,
                mock.patch.object(
                    profiling_asyncio, "_clear_native_task_span", wraps=profiling_asyncio._clear_native_task_span
                ) as clear,
            ):
                profiling_asyncio._publish_task_span(task, None, False)
            finalize.assert_called_once()
            clear.assert_called_once_with(id(task))
            assert id(task) not in profiling_asyncio._task_span_finalizers
            assert await task == 42
        finally:
            _span_links.stop_span_linking()

    asyncio.run(main())


@pytest.mark.subprocess()
def test_custom_factory_preserves_coroutine_names() -> None:
    import asyncio

    from ddtrace.profiling import _span_links

    _span_links.start_span_linking()

    async def child():
        return 42

    async def main():
        def naming_factory(loop, coro, **kwargs):
            return asyncio.Task(coro, loop=loop, name=coro.__qualname__, **kwargs)

        loop = asyncio.get_running_loop()
        loop.set_task_factory(naming_factory)
        try:
            task = asyncio.create_task(child())
            assert task.get_name() == child.__qualname__
            assert task.get_coro().__name__ == child.__name__
            assert await task == 42
        finally:
            loop.set_task_factory(None)
            _span_links.stop_span_linking()

    asyncio.run(main())


@pytest.mark.parametrize("failure_point", ["registry", "callback"])
def test_partial_finalizer_installation_is_detached(monkeypatch, failure_point) -> None:
    finalizers = []

    class RecordingFinalizer(profiling_asyncio.weakref.finalize):
        def __init__(self, *args):
            super().__init__(*args)
            finalizers.append(self)

    class Registry(dict):
        def __setitem__(self, key, value):
            if failure_point == "registry":
                raise MemoryError("registry allocation failed")
            super().__setitem__(key, value)

    class Task:
        def add_done_callback(self, callback, *, context):
            raise RuntimeError("callback registration failed")

    monkeypatch.setattr(profiling_asyncio.weakref, "finalize", RecordingFinalizer)
    monkeypatch.setattr(profiling_asyncio, "_task_span_finalizers", Registry())
    task = Task()
    assert not profiling_asyncio._ensure_task_span_finalizer(task)
    assert not profiling_asyncio._task_span_finalizers
    assert len(finalizers) == 1
    assert not finalizers[0].alive
