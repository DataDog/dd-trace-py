import asyncio
from typing import Optional

import pytest

from ddtrace.internal import core


@pytest.fixture(autouse=True)
def reset_context_and_listeners():
    yield
    core.reset_listeners()
    core._reset_context()


def test_manually_dispatching_end_event():
    """Started auto-dispatches while ended can be manually dispatched."""
    context_id = "context_id"
    called = []

    def on_context_started(ctx):
        called.append(("started", ctx))

    def on_context_ended(ctx, exc_info):
        called.append(("ended", ctx, exc_info))

    core.on("context.started.%s" % context_id, on_context_started)
    core.on("context.ended.%s" % context_id, on_context_ended)
    with core.context_with_data(context_id, dispatch_end_event=False) as ctx:
        pass

    assert called == [("started", ctx)]

    ctx.dispatch_ended_event()
    assert called == [("started", ctx), ("ended", ctx, (None, None, None))]


def test_nested_context_normal_parent_suppressed_child_dispatch_control():
    """Normal parent auto-ends while suppressed child ends only when dispatched."""
    parent_context_id = "parent.context"
    child_context_id = "suppressed.child.context"
    ended = []
    child_ctx: Optional[core.ExecutionContext] = None

    core.on("context.ended.%s" % parent_context_id, lambda ctx, exc_info: ended.append(ctx.identifier))
    core.on("context.ended.%s" % child_context_id, lambda ctx, exc_info: ended.append(ctx.identifier))

    with core.context_with_data(parent_context_id) as parent_ctx:
        assert core.current is parent_ctx
        with core.context_with_data(child_context_id, dispatch_end_event=False) as child_ctx:
            assert core.current is child_ctx
        assert core.current is parent_ctx
        assert ended == []

    assert core.current.identifier == core.ROOT_CONTEXT_ID
    assert ended == [parent_context_id]

    assert child_ctx is not None
    child_ctx.dispatch_ended_event()
    assert core.current.identifier == core.ROOT_CONTEXT_ID
    assert ended == [parent_context_id, child_context_id]


def test_nested_context_suppressed_parent_normal_child_dispatch_control():
    """Normal child auto-ends while suppressed parent ends only when dispatched."""
    parent_context_id = "suppressed.parent.context"
    child_context_id = "normal.child.context"
    ended = []

    core.on("context.ended.%s" % parent_context_id, lambda ctx, exc_info: ended.append(ctx.identifier))
    core.on("context.ended.%s" % child_context_id, lambda ctx, exc_info: ended.append(ctx.identifier))

    with core.context_with_data(parent_context_id, dispatch_end_event=False) as parent_ctx:
        assert core.current is parent_ctx
        with core.context_with_data(child_context_id) as child_ctx:
            assert core.current is child_ctx
        assert core.current is parent_ctx
        assert ended == [child_context_id]
    assert core.current.identifier == core.ROOT_CONTEXT_ID
    assert ended == [child_context_id]

    parent_ctx.dispatch_ended_event()
    assert core.current.identifier == core.ROOT_CONTEXT_ID
    assert ended == [child_context_id, parent_context_id]


def test_error_not_caught_when_manually_dispatching_end_event():
    """Suppressed end dispatch does not swallow exceptions raised in context."""
    context_id = "context_id"
    ended = []

    def on_context_ended(ctx, exc_info):
        ended.append((ctx, exc_info))

    core.on("context.ended.%s" % context_id, on_context_ended)

    with pytest.raises(ValueError):
        with core.context_with_data(context_id, dispatch_end_event=False):
            raise ValueError("OH NO!")

    assert ended == []


def test_manually_report_error():
    """Manual ended event can carry captured exception information."""
    context_id = "context_id"
    ended = []

    def on_context_ended(ctx, exc_info):
        ended.append((ctx, exc_info))

    core.on("context.ended.%s" % context_id, on_context_ended)

    with core.context_with_data(context_id, dispatch_end_event=False) as ctx:
        try:
            raise ValueError("OH NO!")
        except ValueError as e:
            ctx.dispatch_ended_event(type(e), e, e.__traceback__)

            assert ended == [(ctx, (type(e), e, e.__traceback__))]


def test_manually_dispatching_end_event_is_idempotent():
    context_id = "context_id"
    ended = []

    def on_context_ended(ctx, exc_info):
        ended.append((ctx, exc_info))

    core.on("context.ended.%s" % context_id, on_context_ended)
    with core.context_with_data(context_id, dispatch_end_event=False) as ctx:
        pass

    ctx.dispatch_ended_event()
    ctx.dispatch_ended_event()
    assert len(ended) == 1


def test_manual_dispatch_before_context_exit_prevents_auto_double_dispatch():
    """Manual dispatch inside an auto-dispatch context should only emit once."""
    context_id = "context_id"
    ended = []
    ctx: Optional[core.ExecutionContext] = None

    def on_context_ended(ctx, exc_info):
        ended.append((ctx, exc_info))

    core.on("context.ended.%s" % context_id, on_context_ended)

    with pytest.raises(ValueError) as raised:
        with core.context_with_data(context_id) as ctx:
            raise ValueError("__exit__")

    assert ctx is not None
    manual_error = ValueError("manual")
    ctx.dispatch_ended_event(ValueError, manual_error, None)

    assert ended == [(ctx, (ValueError, raised.value, raised.value.__traceback__))]


def test_manual_dispatch_from_async_done_callback_after_context_exit():
    """This test mocks what is happening in aiokafka integration where
    span.finish() is called in a callback that will be executed after context
    exit.
    """

    context_id = "context_id"
    called = []

    def on_context_started(ctx):
        called.append(("started", ctx))

    def on_context_ended(ctx, exc_info):
        called.append(("ended", ctx, exc_info))

    core.on("context.started.%s" % context_id, on_context_started)
    core.on("context.ended.%s" % context_id, on_context_ended)

    async def _run():
        future: Optional[asyncio.Future] = None
        with core.context_with_data(context_id, dispatch_end_event=False) as ctx:
            future = asyncio.get_running_loop().create_future()

            def done_callback(f):
                ctx.dispatch_ended_event()

            future.add_done_callback(done_callback)

        assert called == [("started", ctx)]

        assert future is not None
        future.set_result("ok")
        await asyncio.sleep(0)
        assert called == [("started", ctx), ("ended", ctx, (None, None, None))]

    asyncio.run(_run())


def test_manual_dispatch_when_stream_is_exhausted():
    """This test mocks what is happening in MLObs integration which exits the context
    before finishing the span. It is finished later when all the messages are
    consumed.
    """
    context_id = "context_id"
    called = []

    def on_context_started(ctx):
        called.append(("started", ctx))

    def on_context_ended(ctx, exc_info):
        called.append(("ended", ctx, exc_info))

    core.on("context.started.%s" % context_id, on_context_started)
    core.on("context.ended.%s" % context_id, on_context_ended)

    async def _run():
        stream = None
        with core.context_with_data(context_id, dispatch_end_event=False) as ctx:

            async def _stream():
                yield "chunk-1"
                yield "chunk-2"

            source_stream = _stream()

            async def _wrapped_stream():
                try:
                    async for item in source_stream:
                        yield item
                    ctx.dispatch_ended_event()
                except Exception as e:
                    ctx.dispatch_ended_event(type(e), e, e.__traceback__)
                    raise

            stream = _wrapped_stream()

        # stream is consumed after context exit.
        assert called == [("started", ctx)]
        assert stream is not None
        chunks = [chunk async for chunk in stream]
        assert chunks == ["chunk-1", "chunk-2"]
        assert called == [("started", ctx), ("ended", ctx, (None, None, None))]

    asyncio.run(_run())
