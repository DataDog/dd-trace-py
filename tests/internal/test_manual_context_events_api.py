import asyncio
from typing import Optional
import unittest

import pytest

from ddtrace.internal import core


class TestManualContextEventsApi(unittest.TestCase):
    def tearDown(self):
        core.reset_listeners()
        core._reset_context()

    def test_manually_dispatching_end_event(self):
        """Manual dispatch emits one ended event with empty error info."""
        context_id = "context_id"
        ended = []

        def on_context_ended(ctx, exc_info):
            ended.append((ctx, exc_info))

        core.on("context.ended.%s" % context_id, on_context_ended)
        with core.context_with_data(context_id, dispatch_end_event=False) as ctx:
            pass

        assert ended == []

        ctx.dispatch_ended_event()
        assert len(ended) == 1
        ended_ctx, ended_exc_info = ended[0]
        assert ended_ctx is ctx
        assert ended_exc_info == (None, None, None)

    def test_async_manual_dispatch_end_event_from_different_task(self):
        """Dispatch ended event one second later from async code."""
        context_id = "async.suppressed.context"
        ended = []

        core.on("context.ended.%s" % context_id, lambda ended_ctx, exc_info: ended.append(ended_ctx.identifier))

        with core.context_with_data(context_id, dispatch_end_event=False) as ctx:
            assert core.current is ctx

        assert core.current.identifier == core.ROOT_CONTEXT_ID
        assert ended == []

        async def _dispatch_later():
            await asyncio.sleep(1)
            ctx.dispatch_ended_event()

        asyncio.run(_dispatch_later())

        assert core.current.identifier == core.ROOT_CONTEXT_ID
        assert ended == [context_id]

    def test_nested_context_normal_parent_suppressed_child_dispatch_control(self):
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

    def test_nested_context_suppressed_parent_normal_child_dispatch_control(self):
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

    def test_error_not_caught_when_manually_dispatching_end_event(self):
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

    def test_manually_report_error(self):
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

                ended_ctx, ended_exc_info = ended[0]
                assert ended_ctx is ctx
                assert ended_exc_info[0] is ValueError
                assert isinstance(ended_exc_info[1], ValueError)
                assert ended_exc_info[1].args == ("OH NO!",)

    def test_manually_dispatching_end_event_is_idempotent(self):
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
