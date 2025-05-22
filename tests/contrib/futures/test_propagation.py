import concurrent.futures
import sys
import time

import pytest

from ddtrace.contrib.internal.futures.patch import patch
from ddtrace.contrib.internal.futures.patch import unpatch
from tests.opentracer.utils import init_tracer
from tests.utils import DummyTracer
from tests.utils import TracerTestCase


@pytest.fixture(autouse=True)
def patch_futures():
    patch()
    try:
        yield
    finally:
        unpatch()


class PropagationTestCase(TracerTestCase):
    """Ensures the Context Propagation works between threads
    when the ``futures`` library is used, or when the
    ``concurrent`` module is available (Python 3 only)
    """

    def setUp(self):
        super(PropagationTestCase, self).setUp()

        # instrument ``concurrent``
        patch()

    def tearDown(self):
        # remove instrumentation
        unpatch()

        super(PropagationTestCase, self).tearDown()

    def test_propagation(self):
        # it must propagate the tracing context if available

        def fn():
            with self.tracer.trace("executor.thread"):
                return 42

        with self.override_global_tracer():
            with self.tracer.trace("main.thread"):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    future = executor.submit(fn)
                    result = future.result()
                    # assert the right result
                    self.assertEqual(result, 42)

        # the trace must be completed
        roots = self.get_root_spans()
        assert len(roots) == 1
        root = roots[0]
        assert root.name == "main.thread"
        spans = root.get_spans()
        assert len(spans) == 1
        assert spans[0].name == "executor.thread"
        assert spans[0].trace_id == root.trace_id
        assert spans[0].parent_id == root.span_id

    def test_propagation_with_params(self):
        # instrumentation must proxy arguments if available

        def fn(value, key=None):
            with self.tracer.trace("executor.thread"):
                return value, key

        with self.override_global_tracer():
            with self.tracer.trace("main.thread"):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    future = executor.submit(fn, 42, "CheeseShop")
                    value, key = future.result()
                    # assert the right result
                    self.assertEqual(value, 42)
                    self.assertEqual(key, "CheeseShop")

        # the trace must be completed
        roots = self.get_root_spans()
        assert len(roots) == 1
        root = roots[0]
        assert root.name == "main.thread"
        spans = root.get_spans()
        assert len(spans) == 1
        assert spans[0].name == "executor.thread"
        assert spans[0].trace_id == root.trace_id
        assert spans[0].parent_id == root.span_id

    def test_propagation_with_sub_spans(self):
        @self.tracer.wrap("executor.thread")
        def fn1(value):
            return value

        @self.tracer.wrap("executor.thread")
        def fn2(value):
            return value * 100

        def main(value, key=None):
            return fn1(value) + fn2(value)

        with self.override_global_tracer():
            with self.tracer.trace("main.thread"):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    future = executor.submit(main, 42)
            value = future.result()
            # assert the right result
            self.assertEqual(value, 4242)

        # the trace must be completed
        spans = self.get_spans()
        assert len(spans) == 3
        assert spans[0].name == "main.thread"
        for span in spans[1:]:
            assert span.name == "executor.thread"
            assert span.trace_id == spans[0].trace_id
            assert span.parent_id == spans[0].span_id

    def test_propagation_with_kwargs(self):
        # instrumentation must work if only kwargs are provided

        def fn(value, key=None):
            with self.tracer.trace("executor.thread"):
                return value, key

        with self.override_global_tracer():
            with self.tracer.trace("main.thread"):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    future = executor.submit(fn, value=42, key="CheeseShop")
                    value, key = future.result()
                    # assert the right result
                    self.assertEqual(value, 42)
                    self.assertEqual(key, "CheeseShop")

        # the trace must be completed
        roots = self.get_root_spans()
        assert len(roots) == 1
        root = roots[0]
        assert root.name == "main.thread"
        spans = root.get_spans()
        assert len(spans) == 1
        assert spans[0].name == "executor.thread"
        assert spans[0].trace_id == root.trace_id
        assert spans[0].parent_id == root.span_id

    def test_disabled_instrumentation(self):
        # it must not propagate if the module is disabled
        unpatch()

        def fn():
            with self.tracer.trace("executor.thread"):
                return 42

        with self.override_global_tracer():
            with self.tracer.trace("main.thread"):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    future = executor.submit(fn)
                    result = future.result()
                    # assert the right result
                    self.assertEqual(result, 42)

        # we provide two different traces
        self.assert_span_count(2)

        # Retrieve the root spans (no parents)
        # DEV: Results are sorted based on root span start time
        traces = self.get_root_spans()
        self.assertEqual(len(traces), 2)

        assert traces[0].name == "main.thread"
        assert traces[1].name == "executor.thread"
        assert traces[1].trace_id != traces[0].trace_id
        assert traces[1].parent_id is None

    def test_double_instrumentation(self):
        # double instrumentation must not happen
        patch()

        def fn():
            with self.tracer.trace("executor.thread"):
                return 42

        with self.override_global_tracer():
            with self.tracer.trace("main.thread"):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    future = executor.submit(fn)
                    result = future.result()
                    # assert the right result
                    self.assertEqual(result, 42)

        # the trace must be completed
        root_spans = self.get_root_spans()
        self.assertEqual(len(root_spans), 1)
        root = root_spans[0]
        assert root.name == "main.thread"
        spans = root.get_spans()
        assert len(spans) == 1
        assert spans[0].name == "executor.thread"
        assert spans[0].trace_id == root.trace_id
        assert spans[0].parent_id == root.span_id

    def test_no_parent_span(self):
        def fn():
            with self.tracer.trace("executor.thread"):
                return 42

        with self.override_global_tracer():
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                future = executor.submit(fn)
                result = future.result()
                # assert the right result
                self.assertEqual(result, 42)

        # the trace must be completed
        spans = self.get_spans()
        assert len(spans) == 1
        assert spans[0].name == "executor.thread"
        assert spans[0].parent_id is None

    def test_multiple_futures(self):
        def fn():
            with self.tracer.trace("executor.thread"):
                return 42

        with self.override_global_tracer():
            with self.tracer.trace("main.thread"):
                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                    futures = [executor.submit(fn) for _ in range(4)]
                    for future in futures:
                        result = future.result()
                        # assert the right result
                        self.assertEqual(result, 42)

        # the trace must be completed
        roots = self.get_root_spans()
        assert len(roots) == 1
        root = roots[0]
        assert root.name == "main.thread"

        spans = root.get_spans()
        assert len(spans) == 4
        for span in spans:
            assert span.name == "executor.thread"
            assert span.trace_id == root.trace_id
            assert span.parent_id == root.span_id

    def test_multiple_futures_no_parent(self):
        def fn():
            with self.tracer.trace("executor.thread"):
                return 42

        with self.override_global_tracer():
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                futures = [executor.submit(fn) for _ in range(4)]
                for future in futures:
                    result = future.result()
                    # assert the right result
                    self.assertEqual(result, 42)

        # the trace must be completed
        self.assert_span_count(4)
        root_spans = self.get_root_spans()
        self.assertEqual(len(root_spans), 4)
        for root in root_spans:
            assert root.name == "executor.thread"
            assert root.parent_id is None

    def test_nested_futures(self):
        def fn2():
            with self.tracer.trace("nested.thread"):
                return 42

        def fn():
            with self.tracer.trace("executor.thread"):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    future = executor.submit(fn2)
                    result = future.result()
                    self.assertEqual(result, 42)
                    return result

        with self.override_global_tracer():
            with self.tracer.trace("main.thread"):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    future = executor.submit(fn)
                    result = future.result()
                    # assert the right result
                    self.assertEqual(result, 42)

        # the trace must be completed
        self.assert_span_count(3)
        spans = self.get_spans()
        assert spans[0].name == "main.thread"
        assert spans[1].name == "executor.thread"
        assert spans[1].trace_id == spans[0].trace_id
        assert spans[1].parent_id == spans[0].span_id
        assert spans[2].name == "nested.thread"
        assert spans[2].trace_id == spans[0].trace_id
        assert spans[2].parent_id == spans[1].span_id

    def test_multiple_nested_futures(self):
        def fn2():
            with self.tracer.trace("nested.thread"):
                return 42

        def fn():
            with self.tracer.trace("executor.thread"):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    futures = [executor.submit(fn2) for _ in range(4)]
                    for future in futures:
                        result = future.result()
                        self.assertEqual(result, 42)
                        return result

        with self.override_global_tracer():
            with self.tracer.trace("main.thread"):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    futures = [executor.submit(fn) for _ in range(4)]
                    for future in futures:
                        result = future.result()
                        # assert the right result
                        self.assertEqual(result, 42)

        # the trace must be completed
        traces = self.get_root_spans()
        self.assertEqual(len(traces), 1)

        for root in traces:
            assert root.name == "main.thread"

            exec_spans = root.get_spans()
            assert len(exec_spans) == 4
            for exec_span in exec_spans:
                assert exec_span.name == "executor.thread"
                assert exec_span.trace_id == root.trace_id
                assert exec_span.parent_id == root.span_id

                spans = exec_span.get_spans()
                assert len(spans) == 4
                for i in range(4):
                    assert spans[i].name == "nested.thread"
                    assert spans[i].trace_id == exec_span.trace_id
                    assert spans[i].parent_id == exec_span.span_id

    def test_multiple_nested_futures_no_parent(self):
        def fn2():
            with self.tracer.trace("nested.thread"):
                return 42

        def fn():
            with self.tracer.trace("executor.thread"):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    futures = [executor.submit(fn2) for _ in range(4)]
                    for future in futures:
                        result = future.result()
                        self.assertEqual(result, 42)
                        return result

        with self.override_global_tracer():
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(fn) for _ in range(4)]
                for future in futures:
                    result = future.result()
                    # assert the right result
                    self.assertEqual(result, 42)

        # the trace must be completed
        traces = self.get_root_spans()
        self.assertEqual(len(traces), 4)

        for root in traces:
            assert root.name == "executor.thread"

            spans = root.get_spans()
            assert len(spans) == 4
            for i in range(4):
                assert spans[i].name == "nested.thread"
                assert spans[i].trace_id == root.trace_id
                assert spans[i].parent_id == root.span_id

    def test_send_trace_when_finished(self):
        # it must send the trace only when all threads are finished

        def fn():
            with self.tracer.trace("executor.thread"):
                # wait before returning
                time.sleep(0.05)
                return 42

        with self.override_global_tracer():
            with self.tracer.trace("main.thread"):
                # don't wait for the execution
                executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
                future = executor.submit(fn)
                time.sleep(0.01)

        # then wait for the second thread and send the trace
        result = future.result()
        self.assertEqual(result, 42)

        self.assert_span_count(2)
        spans = self.get_spans()
        assert spans[0].name == "main.thread"
        assert spans[1].name == "executor.thread"
        assert spans[1].trace_id == spans[0].trace_id
        assert spans[1].parent_id == spans[0].span_id

    def test_propagation_ot(self):
        """OpenTracing version of test_propagation."""
        # it must propagate the tracing context if available
        ot_tracer = init_tracer("my_svc", self.tracer)

        def fn():
            # an active context must be available
            self.assertTrue(self.tracer.context_provider.active() is not None)
            with self.tracer.trace("executor.thread"):
                return 42

        with self.override_global_tracer():
            with ot_tracer.start_active_span("main.thread"):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    future = executor.submit(fn)
                    result = future.result()
                    # assert the right result
                    self.assertEqual(result, 42)

        # the trace must be completed
        self.assert_span_count(2)
        spans = self.get_spans()
        assert spans[0].name == "main.thread"
        assert spans[1].name == "executor.thread"
        assert spans[1].trace_id == spans[0].trace_id
        assert spans[1].parent_id == spans[0].span_id


@pytest.mark.skipif(sys.version_info > (3, 12), reason="Fails on 3.13")
@pytest.mark.subprocess(ddtrace_run=True, timeout=5)
def test_concurrent_futures_with_gevent():
    """Check compatibility between the integration and gevent"""
    import os
    import sys

    # Temporarily suppress warnings for Python 3.12 as os.fork() will generate a
    # DeprecationWarning. See https://github.com/python/cpython/pull/100229/
    if sys.version_info >= (3, 12):
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            pid = os.fork()
    else:
        pid = os.fork()

    if pid == 0:
        from gevent import monkey

        monkey.patch_all()
        import concurrent.futures.thread
        from time import sleep

        assert concurrent.futures.thread.__datadog_patch
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            future = executor.submit(lambda: sleep(0.1) or 42)
            result = future.result()
            assert result == 42
        sys.exit(0)
    os.waitpid(pid, 0)


def test_submit_no_wait(tracer: DummyTracer):
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    futures = []

    def work():
        # This is our expected scenario
        assert tracer.current_trace_context() is not None
        assert tracer.current_span() is None

        # DEV: This is the regression case that was raising
        # tracer.current_span().set_tag("work", "done")

        with tracer.trace("work"):
            pass

    def task():
        with tracer.trace("task"):
            for _ in range(4):
                futures.append(executor.submit(work))

    with tracer.trace("main"):
        task()

    # Make sure all background tasks are done
    executor.shutdown(wait=True)

    # Make sure no exceptions were raised in the tasks
    for future in futures:
        assert future.done()
        assert future.exception() is None
        assert future.result() is None

    traces = tracer.pop_traces()
    assert len(traces) == 4

    assert len(traces[0]) == 3
    root_span, task_span, work_span = traces[0]
    assert root_span.name == "main"

    assert task_span.name == "task"
    assert task_span.parent_id == root_span.span_id
    assert task_span.trace_id == root_span.trace_id

    assert work_span.name == "work"
    assert work_span.parent_id == task_span.span_id
    assert work_span.trace_id == root_span.trace_id

    for work_spans in traces[2:]:
        (work_span,) = work_spans
        assert work_span.name == "work"
        assert work_span.parent_id == task_span.span_id
        assert work_span.trace_id == root_span.trace_id
