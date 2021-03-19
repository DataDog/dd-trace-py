import asyncio
import time

import pytest

from ddtrace.compat import CONTEXTVARS_IS_AVAILABLE
from ddtrace.context import Context
from ddtrace.contrib.asyncio.helpers import set_call_context
from ddtrace.contrib.asyncio.patch import patch
from ddtrace.contrib.asyncio.patch import unpatch
from ddtrace.provider import DefaultContextProvider
from tests.opentracer.utils import init_tracer

from .utils import AsyncioTestCase
from .utils import mark_asyncio


_orig_create_task = asyncio.BaseEventLoop.create_task


class TestAsyncioPropagation(AsyncioTestCase):
    """Ensure that asyncio context propagation works between different tasks"""

    def setUp(self):
        # patch asyncio event loop
        patch()
        super(TestAsyncioPropagation, self).setUp()

    def tearDown(self):
        # unpatch asyncio event loop
        unpatch()
        super(TestAsyncioPropagation, self).tearDown()

    @mark_asyncio
    def test_tasks_chaining(self):
        # ensures that the context is propagated between different tasks
        @self.tracer.wrap("spawn_task")
        @asyncio.coroutine
        def coro_3():
            yield from asyncio.sleep(0.01)

        @asyncio.coroutine
        def coro_2():
            # This will have a new context, first run will test that the
            # new context works correctly, second run will test if when we
            # pop off the last span on the context if it is still parented
            # correctly
            yield from coro_3()
            yield from coro_3()

        @self.tracer.wrap("main_task")
        @asyncio.coroutine
        def coro_1():
            yield from asyncio.ensure_future(coro_2())

        yield from coro_1()

        traces = self.pop_traces()
        assert len(traces) == 3
        assert len(traces[0]) == 1
        assert len(traces[1]) == 1
        main_task = traces[-1][0]
        spawn_task1 = traces[0][0]
        spawn_task2 = traces[1][0]
        # check if the context has been correctly propagated
        assert spawn_task1.trace_id == main_task.trace_id
        assert spawn_task1.parent_id == main_task.span_id

        assert spawn_task2.trace_id == main_task.trace_id
        assert spawn_task2.parent_id == main_task.span_id

    @mark_asyncio
    def test_concurrent_chaining(self):
        # ensures that the context is correctly propagated when
        # concurrent tasks are created from a common tracing block
        @self.tracer.wrap("f1")
        @asyncio.coroutine
        def f1():
            yield from asyncio.sleep(0.01)

        @self.tracer.wrap("f2")
        @asyncio.coroutine
        def f2():
            yield from asyncio.sleep(0.01)

        with self.tracer.trace("main_task"):
            yield from asyncio.gather(f1(), f2())
            # do additional synchronous work to confirm main context is
            # correctly handled
            with self.tracer.trace("main_task_child"):
                time.sleep(0.01)

        traces = self.pop_traces()
        assert len(traces) == 3
        assert len(traces[0]) == 1
        assert len(traces[1]) == 1
        assert len(traces[2]) == 2
        child_1 = traces[0][0]
        child_2 = traces[1][0]
        main_task = traces[2][0]
        main_task_child = traces[2][1]
        # check if the context has been correctly propagated
        assert child_1.trace_id == main_task.trace_id
        assert child_1.parent_id == main_task.span_id
        assert child_2.trace_id == main_task.trace_id
        assert child_2.parent_id == main_task.span_id
        assert main_task_child.trace_id == main_task.trace_id
        assert main_task_child.parent_id == main_task.span_id

    @pytest.mark.skipif(CONTEXTVARS_IS_AVAILABLE, reason="only applicable to legacy asyncio provider")
    @mark_asyncio
    def test_propagation_with_set_call_context(self):
        # ensures that if a new Context is attached to the current
        # running Task via helpers, a previous trace is resumed
        task = asyncio.Task.current_task()
        ctx = Context(trace_id=100, span_id=101)
        set_call_context(task, ctx)

        with self.tracer.trace("async_task"):
            yield from asyncio.sleep(0.01)

        traces = self.pop_traces()
        assert len(traces) == 1
        assert len(traces[0]) == 1
        span = traces[0][0]
        assert span.trace_id == 100
        assert span.parent_id == 101

    @mark_asyncio
    def test_propagation_with_new_context(self):
        # ensures that if a new Context is activated, a trace
        # with the Context arguments is created
        ctx = Context(trace_id=100, span_id=101)
        self.tracer.context_provider.activate(ctx)

        with self.tracer.trace("async_task"):
            yield from asyncio.sleep(0.01)

        traces = self.pop_traces()
        assert len(traces) == 1
        assert len(traces[0]) == 1
        span = traces[0][0]
        assert span.trace_id == 100
        assert span.parent_id == 101

    @mark_asyncio
    def test_event_loop_unpatch(self):
        # ensures that the event loop can be unpatched
        unpatch()
        assert isinstance(self.tracer.context_provider, DefaultContextProvider)
        assert asyncio.BaseEventLoop.create_task == _orig_create_task

    def test_event_loop_double_patch(self):
        # ensures that double patching will not double instrument
        # the event loop
        patch()
        self.test_tasks_chaining()

    @mark_asyncio
    def test_trace_multiple_coroutines_ot_outer(self):
        """OpenTracing version of test_trace_multiple_coroutines."""
        # if multiple coroutines have nested tracing, they must belong
        # to the same trace
        @asyncio.coroutine
        def coro():
            # another traced coroutine
            with self.tracer.trace("coroutine_2"):
                return 42

        ot_tracer = init_tracer("asyncio_svc", self.tracer)
        with ot_tracer.start_active_span("coroutine_1"):
            value = yield from coro()

        # the coroutine has been called correctly
        assert 42 == value
        # a single trace has been properly reported
        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 2 == len(traces[0])
        assert "coroutine_1" == traces[0][0].name
        assert "coroutine_2" == traces[0][1].name
        # the parenting is correct
        assert traces[0][0] == traces[0][1]._parent
        assert traces[0][0].trace_id == traces[0][1].trace_id

    @mark_asyncio
    def test_trace_multiple_coroutines_ot_inner(self):
        """OpenTracing version of test_trace_multiple_coroutines."""
        # if multiple coroutines have nested tracing, they must belong
        # to the same trace
        ot_tracer = init_tracer("asyncio_svc", self.tracer)

        @asyncio.coroutine
        def coro():
            # another traced coroutine
            with ot_tracer.start_active_span("coroutine_2"):
                return 42

        with self.tracer.trace("coroutine_1"):
            value = yield from coro()

        # the coroutine has been called correctly
        assert 42 == value
        # a single trace has been properly reported
        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 2 == len(traces[0])
        assert "coroutine_1" == traces[0][0].name
        assert "coroutine_2" == traces[0][1].name
        # the parenting is correct
        assert traces[0][0] == traces[0][1]._parent
        assert traces[0][0].trace_id == traces[0][1].trace_id
