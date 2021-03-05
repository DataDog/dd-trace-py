import asyncio

from ddtrace.contrib.asyncio.compat import asyncio_current_task

from .utils import AsyncioTestCase
from .utils import mark_asyncio


class TestAsyncioTracer(AsyncioTestCase):
    """Ensure that the tracer works with asynchronous executions within
    the same ``IOLoop``.
    """

    @mark_asyncio
    def test_get_call_context_twice(self):
        # it should return the same Context if called twice
        assert self.tracer.get_call_context() == self.tracer.get_call_context()

    @mark_asyncio
    def test_trace_coroutine(self):
        # it should use the task context when invoked in a coroutine
        with self.tracer.trace("coroutine") as span:
            span.resource = "base"

        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])
        assert "coroutine" == traces[0][0].name
        assert "base" == traces[0][0].resource

    @mark_asyncio
    def test_trace_multiple_coroutines(self):
        # if multiple coroutines have nested tracing, they must belong
        # to the same trace
        @asyncio.coroutine
        def coro():
            # another traced coroutine
            with self.tracer.trace("coroutine_2"):
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

    @mark_asyncio
    def test_event_loop_exception(self):
        # it should handle a loop exception
        asyncio.set_event_loop(None)
        ctx = self.tracer.get_call_context()
        assert ctx is not None

    def test_context_task_none(self):
        # it should handle the case where a Task is not available
        # Note: the @mark_asyncio is missing to simulate an execution
        # without a Task
        task = asyncio_current_task()
        # the task is not available
        assert task is None
        # but a new Context is still created making the operation safe
        ctx = self.tracer.get_call_context()
        assert ctx is not None

    @mark_asyncio
    def test_exception(self):
        @asyncio.coroutine
        def f1():
            with self.tracer.trace("f1"):
                raise Exception("f1 error")

        with self.assertRaises(Exception):
            yield from f1()
        traces = self.pop_traces()
        assert 1 == len(traces)
        spans = traces[0]
        assert 1 == len(spans)
        span = spans[0]
        assert 1 == span.error
        assert "f1 error" == span.get_tag("error.msg")
        assert "Exception: f1 error" in span.get_tag("error.stack")

    @mark_asyncio
    def test_nested_exceptions(self):
        @asyncio.coroutine
        def f1():
            with self.tracer.trace("f1"):
                raise Exception("f1 error")

        @asyncio.coroutine
        def f2():
            with self.tracer.trace("f2"):
                yield from f1()

        with self.assertRaises(Exception):
            yield from f2()

        traces = self.pop_traces()
        assert 1 == len(traces)
        spans = traces[0]
        assert 2 == len(spans)
        span = spans[0]
        assert "f2" == span.name
        assert 1 == span.error  # f2 did not catch the exception
        assert "f1 error" == span.get_tag("error.msg")
        assert "Exception: f1 error" in span.get_tag("error.stack")
        span = spans[1]
        assert "f1" == span.name
        assert 1 == span.error
        assert "f1 error" == span.get_tag("error.msg")
        assert "Exception: f1 error" in span.get_tag("error.stack")

    @mark_asyncio
    def test_handled_nested_exceptions(self):
        @asyncio.coroutine
        def f1():
            with self.tracer.trace("f1"):
                raise Exception("f1 error")

        @asyncio.coroutine
        def f2():
            with self.tracer.trace("f2"):
                try:
                    yield from f1()
                except Exception:
                    pass

        yield from f2()

        traces = self.pop_traces()
        assert 1 == len(traces)
        spans = traces[0]
        assert 2 == len(spans)
        span = spans[0]
        assert "f2" == span.name
        assert 0 == span.error  # f2 caught the exception
        span = spans[1]
        assert "f1" == span.name
        assert 1 == span.error
        assert "f1 error" == span.get_tag("error.msg")
        assert "Exception: f1 error" in span.get_tag("error.stack")

    @mark_asyncio
    def test_trace_multiple_calls(self):
        # create multiple futures so that we expect multiple
        # traces instead of a single one (helper not used)
        @asyncio.coroutine
        def coro():
            # another traced coroutine
            with self.tracer.trace("coroutine"):
                yield from asyncio.sleep(0.01)

        futures = [asyncio.ensure_future(coro()) for x in range(10)]
        for future in futures:
            yield from future

        traces = self.pop_traces()
        assert 10 == len(traces)
        assert 1 == len(traces[0])
        assert "coroutine" == traces[0][0].name

    @mark_asyncio
    def test_wrapped_coroutine(self):
        @self.tracer.wrap("f1")
        @asyncio.coroutine
        def f1():
            yield from asyncio.sleep(0.25)

        yield from f1()

        traces = self.pop_traces()
        assert 1 == len(traces)
        spans = traces[0]
        assert 1 == len(spans)
        span = spans[0]
        assert span.duration > 0.25, "span.duration={}".format(span.duration)
