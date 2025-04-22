from collections import Counter
import os
import subprocess
from time import sleep

import celery
from celery.exceptions import Retry
import mock
import pytest

from ddtrace.constants import ERROR_MSG
from ddtrace.contrib.internal.celery.patch import patch
from ddtrace.contrib.internal.celery.patch import unpatch
import ddtrace.internal.forksafe as forksafe
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.trace import Context
from ddtrace.trace import Pin
from tests.opentracer.utils import init_tracer

from ...utils import override_global_config
from .base import CeleryBaseTestCase


class MyException(Exception):
    pass


class CeleryIntegrationTask(CeleryBaseTestCase):
    """Ensures that the tracer works properly with a real Celery application
    without breaking the Application or Task API.
    """

    def tearDown(self):
        super(CeleryIntegrationTask, self).tearDown()
        if os.path.isfile("celerybeat-schedule.dir"):
            os.remove("celerybeat-schedule.bak")
            os.remove("celerybeat-schedule.dat")
            os.remove("celerybeat-schedule.dir")

    def test_concurrent_delays(self):
        # it should create one trace for each delayed execution
        @self.app.task
        def fn_task():
            return 42

        results = [fn_task.delay() for _ in range(100)]

        for result in results:
            assert result.get(timeout=self.ASYNC_GET_TIMEOUT) == 42

        # Wait for all spans to finish
        sleep(0.5)
        traces = self.pop_traces()
        assert len(traces) == (200 if self.ASYNC_USE_CELERY_FIXTURES else 100)

    def test_idempotent_patch(self):
        # calling patch() twice doesn't have side effects
        patch()

        @self.app.task
        def fn_task():
            return 42

        t = fn_task.apply()
        assert t.successful()
        assert 42 == t.result

        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])

    def test_idempotent_unpatch(self):
        # calling unpatch() twice doesn't have side effects
        unpatch()
        unpatch()

        @self.app.task
        def fn_task():
            return 42

        t = fn_task.apply()
        assert t.successful()
        assert 42 == t.result

        traces = self.pop_traces()
        assert 0 == len(traces)

    def test_fn_task_run(self):
        # the body of the function is not instrumented so calling it
        # directly doesn't create a trace
        @self.app.task
        def fn_task():
            return 42

        t = fn_task.run()
        assert t == 42

        traces = self.pop_traces()
        assert 0 == len(traces)

    def test_fn_task_call(self):
        # the body of the function is not instrumented so calling it
        # directly doesn't create a trace
        @self.app.task
        def fn_task():
            return 42

        t = fn_task()
        assert t == 42

        traces = self.pop_traces()
        assert 0 == len(traces)

    def test_fn_task_apply(self):
        # it should execute a traced task with a returning value
        @self.app.task
        def fn_task():
            return 42

        t = fn_task.apply()
        assert t.successful()
        assert 42 == t.result

        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])
        span = traces[0][0]

        self.assert_is_measured(span)
        assert span.error == 0
        assert span.name == "celery.run"
        assert span.resource == "tests.contrib.celery.test_integration.fn_task"
        assert span.service == "celery-worker"
        assert span.span_type == "worker"
        assert span.get_tag("celery.id") == t.task_id
        assert span.get_tag("celery.action") == "run"
        assert span.get_tag("celery.state") == "SUCCESS"
        assert span.get_tag("component") == "celery"
        assert span.get_tag("span.kind") == "consumer"

    def test_fn_task_apply_bind(self):
        # it should execute a traced task with a returning value
        @self.app.task(bind=True)
        def fn_task(self):
            return self

        t = fn_task.apply()
        assert t.successful()
        assert "fn_task" in t.result.name

        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])
        span = traces[0][0]

        self.assert_is_measured(span)
        assert span.error == 0
        assert span.name == "celery.run"
        assert span.resource == "tests.contrib.celery.test_integration.fn_task"
        assert span.service == "celery-worker"
        assert span.get_tag("celery.id") == t.task_id
        assert span.get_tag("celery.action") == "run"
        assert span.get_tag("celery.state") == "SUCCESS"
        assert span.get_tag("component") == "celery"
        assert span.get_tag("span.kind") == "consumer"

    def test_fn_task_apply_async(self):
        # it should execute a traced async task that has parameters
        @self.app.task
        def fn_task_parameters(user, force_logout=False):
            return (user, force_logout)

        t = fn_task_parameters.apply_async(args=["user"], kwargs={"force_logout": True})
        assert tuple(t.get(timeout=self.ASYNC_GET_TIMEOUT)) == ("user", True)

        traces = self.pop_traces()

        if self.ASYNC_USE_CELERY_FIXTURES:
            assert 2 == len(traces)
            assert 1 == len(traces[0])
            assert 1 == len(traces[1])
            async_span = traces[0][0]
            run_span = traces[1][0]

            self.assert_is_measured(async_span)
            assert async_span.error == 0
            assert async_span.name == "celery.apply"
            assert async_span.resource == "tests.contrib.celery.test_integration.fn_task_parameters"
            assert async_span.service == "celery-producer"
            assert async_span.get_tag("celery.id") == t.task_id
            assert async_span.get_tag("celery.action") == "apply_async"
            assert async_span.get_tag("celery.routing_key") == "celery"
            assert async_span.get_tag("component") == "celery"
            assert async_span.get_tag("span.kind") == "producer"
            assert async_span.get_tag("out.host") == "memory://"
        else:
            assert 1 == len(traces)
            assert 1 == len(traces[0])
            run_span = traces[0][0]

        self.assert_is_measured(run_span)
        assert run_span.error == 0
        assert run_span.name == "celery.run"
        assert run_span.resource == "tests.contrib.celery.test_integration.fn_task_parameters"
        assert run_span.service == "celery-worker"
        assert run_span.get_tag("celery.id") == t.task_id
        assert run_span.get_tag("celery.action") == "run"
        assert run_span.get_tag("component") == "celery"
        assert run_span.get_tag("span.kind") == "consumer"

    def test_fn_task_delay(self):
        # using delay shorthand must preserve arguments
        @self.app.task
        def fn_task_parameters(user, force_logout=False):
            return (user, force_logout)

        t = fn_task_parameters.delay("user", force_logout=True)
        assert tuple(t.get(timeout=self.ASYNC_GET_TIMEOUT)) == ("user", True)

        traces = self.pop_traces()

        if self.ASYNC_USE_CELERY_FIXTURES:
            assert 2 == len(traces)
            assert 1 == len(traces[0])
            assert 1 == len(traces[1])
            async_span = traces[0][0]
            run_span = traces[1][0]

            self.assert_is_measured(async_span)
            assert async_span.error == 0
            assert async_span.name == "celery.apply"
            assert async_span.resource == "tests.contrib.celery.test_integration.fn_task_parameters"
            assert async_span.service == "celery-producer"
            assert async_span.get_tag("celery.id") == t.task_id
            assert async_span.get_tag("celery.action") == "apply_async"
            assert async_span.get_tag("celery.routing_key") == "celery"
            assert async_span.get_tag("component") == "celery"
            assert async_span.get_tag("span.kind") == "producer"
            assert async_span.get_tag("out.host") == "memory://"
        else:
            assert 1 == len(traces)
            assert 1 == len(traces[0])
            run_span = traces[0][0]

        self.assert_is_measured(run_span)
        assert run_span.error == 0
        assert run_span.name == "celery.run"
        assert run_span.resource == "tests.contrib.celery.test_integration.fn_task_parameters"
        assert run_span.service == "celery-worker"
        assert run_span.get_tag("celery.id") == t.task_id
        assert run_span.get_tag("celery.action") == "run"
        assert run_span.get_tag("component") == "celery"
        assert run_span.get_tag("span.kind") == "consumer"

    def test_fn_exception(self):
        # it should catch exceptions in task functions
        @self.app.task
        def fn_exception():
            raise Exception("Task class is failing")

        t = fn_exception.apply()
        assert t.failed()
        assert "Task class is failing" in t.traceback

        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])
        span = traces[0][0]

        self.assert_is_measured(span)
        assert span.name == "celery.run"
        assert span.resource == "tests.contrib.celery.test_integration.fn_exception"
        assert span.service == "celery-worker"
        assert span.get_tag("celery.id") == t.task_id
        assert span.get_tag("celery.action") == "run"
        assert span.get_tag("celery.state") == "FAILURE"
        assert span.error == 1
        assert span.get_tag("component") == "celery"
        assert span.get_tag("span.kind") == "consumer"
        assert span.get_tag(ERROR_MSG) == "Task class is failing"
        assert "Traceback (most recent call last)" in span.get_tag("error.stack")
        assert "Task class is failing" in span.get_tag("error.stack")

    def test_fn_exception_expected(self):
        # it should catch exceptions in task functions
        @self.app.task(throws=(MyException,))
        def fn_exception():
            raise MyException("Task class is failing")

        t = fn_exception.apply()
        assert t.failed()
        assert "Task class is failing" in t.traceback

        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])
        span = traces[0][0]

        self.assert_is_measured(span)
        assert span.name == "celery.run"
        assert span.resource == "tests.contrib.celery.test_integration.fn_exception"
        assert span.service == "celery-worker"
        assert span.get_tag("celery.id") == t.task_id
        assert span.get_tag("celery.action") == "run"
        assert span.get_tag("celery.state") == "FAILURE"
        assert span.get_tag("component") == "celery"
        assert span.get_tag("span.kind") == "consumer"
        assert span.error == 0

    def test_fn_retry_exception(self):
        # it should not catch retry exceptions in task functions
        @self.app.task
        def fn_exception():
            raise Retry("Task class is being retried")

        t = fn_exception.apply()
        assert not t.failed()
        assert "Task class is being retried" in t.traceback

        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])
        span = traces[0][0]

        self.assert_is_measured(span)
        assert span.name == "celery.run"
        assert span.resource == "tests.contrib.celery.test_integration.fn_exception"
        assert span.service == "celery-worker"
        assert span.get_tag("celery.id") == t.task_id
        assert span.get_tag("celery.action") == "run"
        assert span.get_tag("celery.state") == "RETRY"
        assert span.get_tag("celery.retry.reason") == "Task class is being retried"
        assert span.get_tag("component") == "celery"
        assert span.get_tag("span.kind") == "consumer"

        # This type of retrying should not be marked as an exception
        assert span.error == 0
        assert not span.get_tag(ERROR_MSG)
        assert not span.get_tag("error.stack")

    def test_class_task(self):
        # it should execute class based tasks with a returning value
        class BaseTask(self.app.Task):
            def run(self):
                return 42

        t = BaseTask()
        # register the Task class if it's available (required in Celery 4.0+)
        register_task = getattr(self.app, "register_task", None)
        if register_task is not None:
            register_task(t)

        r = t.apply()
        assert r.successful()
        assert 42 == r.result

        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])
        span = traces[0][0]

        self.assert_is_measured(span)
        assert span.error == 0
        assert span.name == "celery.run"
        assert span.resource == "tests.contrib.celery.test_integration.BaseTask"
        assert span.service == "celery-worker"
        assert span.get_tag("celery.id") == r.task_id
        assert span.get_tag("celery.action") == "run"
        assert span.get_tag("celery.state") == "SUCCESS"
        assert span.get_tag("component") == "celery"
        assert span.get_tag("span.kind") == "consumer"

    def test_class_task_exception(self):
        # it should catch exceptions in class based tasks
        class BaseTask(self.app.Task):
            def run(self):
                raise Exception("Task class is failing")

        t = BaseTask()
        # register the Task class if it's available (required in Celery 4.0+)
        register_task = getattr(self.app, "register_task", None)
        if register_task is not None:
            register_task(t)

        r = t.apply()
        assert r.failed()
        assert "Task class is failing" in r.traceback

        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])
        span = traces[0][0]

        self.assert_is_measured(span)
        assert span.name == "celery.run"
        assert span.resource == "tests.contrib.celery.test_integration.BaseTask"
        assert span.service == "celery-worker"
        assert span.get_tag("celery.id") == r.task_id
        assert span.get_tag("celery.action") == "run"
        assert span.get_tag("celery.state") == "FAILURE"
        assert span.error == 1
        assert span.get_tag("component") == "celery"
        assert span.get_tag(ERROR_MSG) == "Task class is failing"
        assert "Traceback (most recent call last)" in span.get_tag("error.stack")
        assert "Task class is failing" in span.get_tag("error.stack")
        assert span.get_tag("span.kind") == "consumer"

    def test_class_task_exception_expected(self):
        # it should catch exceptions in class based tasks
        class BaseTask(self.app.Task):
            throws = (MyException,)

            def run(self):
                raise MyException("Task class is failing")

        t = BaseTask()
        # register the Task class if it's available (required in Celery 4.0+)
        register_task = getattr(self.app, "register_task", None)
        if register_task is not None:
            register_task(t)

        r = t.apply()
        assert r.failed()
        assert "Task class is failing" in r.traceback

        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])
        span = traces[0][0]

        self.assert_is_measured(span)
        assert span.name == "celery.run"
        assert span.resource == "tests.contrib.celery.test_integration.BaseTask"
        assert span.service == "celery-worker"
        assert span.get_tag("celery.id") == r.task_id
        assert span.get_tag("celery.action") == "run"
        assert span.get_tag("celery.state") == "FAILURE"
        assert span.get_tag("component") == "celery"
        assert span.get_tag("span.kind") == "consumer"
        assert span.error == 0

    def test_task_chain_same_trace(self):
        @self.app.task(max_retries=1, default_retry_delay=1)
        def fn_b(user, force_logout=False):
            raise ValueError("Foo")

        self.celery_worker.reload()  # Reload after each task or we get an unregistered error

        @self.app.task(bind=True, max_retries=1, autoretry_for=(Exception,), default_retry_delay=1)
        def fn_a(self, user, force_logout=False):
            fn_b.apply_async(args=[user], kwargs={"force_logout": force_logout})
            raise ValueError("foo")

        self.celery_worker.reload()  # Reload after each task or we get an unregistered error

        traces = None
        try:
            with self.override_config("celery", dict(distributed_tracing=True)):
                t = fn_a.apply_async(args=["user"], kwargs={"force_logout": True})
                # We wait 10 seconds so all tasks finish.  While it'd be nice to block
                # until all tasks complete, celery doesn't offer an option. Using get()
                # causes a deadlock, since in test-mode we only have one worker.
                import time

                time.sleep(10)
                t.get()
        except Exception:
            pass

        traces = self.pop_traces()
        # The below tests we have 1 trace with 8 spans, which is the shape generated
        assert len(traces) > 0
        assert sum([1 for trace in traces for span in trace]) == 8
        trace_id = traces[0][0].trace_id
        assert all(trace_id == span.trace_id for trace in traces for span in trace)

    @mock.patch("kombu.messaging.Producer.publish", mock.Mock(side_effect=ValueError))
    def test_fn_task_apply_async_soft_exception(self):
        # If the underlying library runs into an exception that doesn't crash the app
        # while calling apply_async, we should still close the span even
        # if the closing signals didn't get called and mark the span as an error

        @self.app.task
        def fn_task_parameters(user, force_logout=False):
            return (user, force_logout)

        t = None
        try:
            t = fn_task_parameters.apply_async(args=["user"], kwargs={"force_logout": True})
        except ValueError:
            traces = self.pop_traces()
            assert 1 == len(traces)
            assert traces[0][0].name == "celery.apply"
            assert traces[0][0].resource == "tests.contrib.celery.test_integration.fn_task_parameters"
            assert traces[0][0].get_tag("celery.action") == "apply_async"
            assert traces[0][0].get_tag("component") == "celery"
            assert traces[0][0].get_tag("span.kind") == "producer"
            # Internal library errors get recorded on the span
            assert traces[0][0].error == 1
            assert traces[0][0].get_tag("error.type") == "builtins.ValueError"
            assert "ValueError" in traces[0][0].get_tag("error.stack")
            # apply_async runs into an internal error (ValueError) so nothing is returned to t
            assert t is None

    def test_shared_task(self):
        # Ensure Django Shared Task are supported
        @celery.shared_task
        def add(x, y):
            return x + y

        res = add.apply([2, 2])
        assert res.result == 4

        traces = self.pop_traces()
        assert 1 == len(traces)
        assert 1 == len(traces[0])
        span = traces[0][0]

        self.assert_is_measured(span)
        assert span.error == 0
        assert span.name == "celery.run"
        assert span.service == "celery-worker"
        assert span.resource == "tests.contrib.celery.test_integration.add"
        assert span.parent_id is None
        assert span.get_tag("celery.id") == res.task_id
        assert span.get_tag("celery.action") == "run"
        assert span.get_tag("celery.state") == "SUCCESS"
        assert span.get_tag("component") == "celery"
        assert span.get_tag("span.kind") == "consumer"

    def test_worker_service_name(self):
        @self.app.task
        def fn_task():
            return 42

        # Ensure worker service name can be changed via
        # configuration object
        with self.override_config("celery", dict(worker_service_name="worker-notify")):
            t = fn_task.apply()
            self.assertTrue(t.successful())
            self.assertEqual(42, t.result)

            traces = self.pop_traces()
            self.assertEqual(1, len(traces))
            self.assertEqual(1, len(traces[0]))
            span = traces[0][0]
            self.assertEqual(span.service, "worker-notify")

    def test_producer_service_name(self):
        @self.app.task
        def fn_task():
            return 42

        # Ensure producer service name can be changed via
        # configuration object
        with self.override_config("celery", dict(producer_service_name="task-queue", worker_service_name="task-queue")):
            t = fn_task.delay()
            assert t.get(timeout=self.ASYNC_GET_TIMEOUT) == 42

            traces = self.pop_traces()

            if self.ASYNC_USE_CELERY_FIXTURES:
                assert 2 == len(traces)
                assert 1 == len(traces[0])
                assert 1 == len(traces[1])
                async_span = traces[0][0]
                run_span = traces[1][0]
                assert async_span.service == "task-queue"
            else:
                assert 1 == len(traces)
                assert 1 == len(traces[0])
                run_span = traces[0][0]

            assert run_span.service == "task-queue"

    def test_trace_in_task(self):
        @self.app.task
        def fn_task():
            with self.tracer.trace("test"):
                return 42

        t = fn_task.delay()
        assert t.get(timeout=self.ASYNC_GET_TIMEOUT) == 42
        traces = self.pop_traces()

        if self.ASYNC_USE_CELERY_FIXTURES:
            assert len(traces) == 2
            trace_map = {len(t): t for t in traces}
            assert 2 in trace_map
            assert 1 in trace_map

            apply_trace = trace_map[1]
            assert apply_trace[0].name == "celery.apply"
            run_trace = trace_map[2]
        else:
            run_trace = traces[0]

        assert run_trace[0].name == "celery.run"
        assert run_trace[1].name == "test"
        assert run_trace[1].parent_id == run_trace[0].span_id

    def test_fn_task_apply_async_ot(self):
        """OpenTracing version of test_fn_task_apply_async."""
        ot_tracer = init_tracer("celery_svc", self.tracer)

        # it should execute a traced async task that has parameters
        @self.app.task
        def fn_task_parameters(user, force_logout=False):
            return (user, force_logout)

        with ot_tracer.start_active_span("celery_op"):
            t = fn_task_parameters.apply_async(args=["user"], kwargs={"force_logout": True})
            assert tuple(t.get(timeout=self.ASYNC_GET_TIMEOUT)) == ("user", True)

        ot_span = self.find_span(name="celery_op")
        assert ot_span.parent_id is None
        assert ot_span.name == "celery_op"
        assert ot_span.service == "celery_svc"

        if self.ASYNC_USE_CELERY_FIXTURES:
            async_span = self.find_span(name="celery.apply")
            self.assert_is_measured(async_span)
            assert async_span.error == 0

            # confirm the parenting
            assert async_span.parent_id == ot_span.span_id
            assert async_span.name == "celery.apply"
            assert async_span.resource == "tests.contrib.celery.test_integration.fn_task_parameters"
            assert async_span.service == "celery-producer"
            assert async_span.get_tag("celery.id") == t.task_id
            assert async_span.get_tag("celery.action") == "apply_async"
            assert async_span.get_tag("celery.routing_key") == "celery"
            assert async_span.get_tag("component") == "celery"
            assert async_span.get_tag("span.kind") == "producer"
            assert async_span.get_tag("out.host") == "memory://"

        run_span = self.find_span(name="celery.run")
        assert run_span.name == "celery.run"
        assert run_span.parent_id is None
        assert run_span.resource == "tests.contrib.celery.test_integration.fn_task_parameters"
        assert run_span.service == "celery-worker"
        assert run_span.get_tag("celery.id") == t.task_id
        assert run_span.get_tag("celery.action") == "run"
        assert run_span.get_tag("component") == "celery"
        assert run_span.get_tag("span.kind") == "consumer"

        traces = self.pop_traces()
        assert len(traces) == 2
        assert len(traces[0]) + len(traces[1]) == 3

    @pytest.mark.no_getattr_patch
    def test_beat_scheduler_tracing(self):
        @self.app.task
        def fn_task():
            return 42

        # Avoid the time to start the TelemetryWriter since this is a time-sensitive test:
        with override_global_config(dict(_telemetry_enabled=False)):
            target_task_run_count = 2
            run_time_seconds = 1.0

            self.app.conf.beat_schedule = {
                "mytestschedule": {
                    "task": "tests.contrib.celery.test_integration.fn_task",
                    "schedule": run_time_seconds / target_task_run_count,
                }
            }

            beat_service = celery.beat.EmbeddedService(self.app, thread=True)
            beat_service.start()
            sleep(run_time_seconds + 0.3)
            beat_service.stop()

            actual_run_count = beat_service.service.get_scheduler().schedule["mytestschedule"].total_run_count
            traces = self.pop_traces()
            assert len(traces) >= actual_run_count
            assert traces[0][0].name == "celery.beat.tick"

            # the following code verifies a trace structure in which every root span is either "celery.beat.tick" or
            # "celery.run".
            # some "celery.beat.tick" spans have no children, indicating that the beat scheduler checked for "due"
            # tasks and found none.
            # some "celery.beat.tick" spans have two children, "celery.beat.apply_entry" and "celery.apply".
            # "celery.beat.apply_entry" is celery.beat's wrapper around apply_async, which triggers "celery.apply"
            # and an asynchronous "celery.run" call.
            # these "deep traces" indicate tick() calls that found a "due" task and triggered it

            spans_counter = Counter()
            deep_traces_count = 0
            for trace in traces:
                span_name = trace[0].name
                spans_counter.update([span_name])
                if span_name == "celery.beat.tick" and len(trace) > 1:
                    deep_traces_count += 1
                    spans_counter.update([trace[1].name, trace[2].name])
                    assert trace[1].name == "celery.beat.apply_entry"
                    assert trace[2].name == "celery.apply"
            # the number of task runs that beat schedules in this test is unpredictable
            # luckily this test doesn't care about the specific number of runs as long as it's
            # sufficiently large to cover the number of invocations we expect
            assert deep_traces_count >= target_task_run_count
            assert deep_traces_count == spans_counter["celery.beat.apply_entry"]
            assert deep_traces_count == spans_counter["celery.apply"]
            # beat_service.stop() can happen any time during the beat thread's execution.
            # When by chance it happens between apply_entry() and run(), the run() span will be
            # omitted, resulting in one fewer span for run() than the other functions
            assert actual_run_count >= spans_counter["celery.run"]


class CeleryDistributedTracingIntegrationTask(CeleryBaseTestCase):
    """Distributed tracing is tricky to test for two reasons:

    1. We aren't running anything distributed at all in this test suite
    2. Celery doesn't run the `before_task_publish` signal we rely
       on when running in synchronous mode, e.g. using apply.
       https://github.com/celery/celery/issues/3864

    To get around #1, we inject our own new context in a prerun signal
    to simulate a distributed worker beginning with its own context
    (which does not match the parent context from the publisher).

    Hopefully if #2 is ever fixed we can more robustly test both of the
    signals we're using for distributed trace propagation. For now,
    this is only really testing the prerun part of the distributed tracing,
    and it needs to simulate the before_publish part. :(
    """

    def setUp(self):
        super(CeleryDistributedTracingIntegrationTask, self).setUp()
        provider = Pin.get_from(self.app).tracer.context_provider
        provider.activate(Context(trace_id=12345, span_id=12345, sampling_priority=1))

    def tearDown(self):
        super(CeleryDistributedTracingIntegrationTask, self).tearDown()

    # override instrumenting fixture to add prerrun signal for context setting
    @pytest.fixture(autouse=True)
    def instrument_celery(self):
        # Register our context-ruining signal before the normal ones so that
        # the "real" task_prerun signal starts with the new context
        celery.signals.task_prerun.connect(self.inject_new_context)

        # instrument Celery and create an app with Broker and Result backends
        patch()
        yield

        # remove instrumentation from Celery
        unpatch()

        celery.signals.task_prerun.disconnect(self.inject_new_context)

    def inject_new_context(self, *args, **kwargs):
        pin = Pin.get_from(self.app)
        pin.tracer.context_provider.activate(Context(trace_id=99999, span_id=99999, sampling_priority=1))

    def test_distributed_tracing_disabled(self):
        """This test is just making sure our signal hackery in this test class
        is working the way we expect it to.
        """

        @self.app.task
        def fn_task():
            return 42

        fn_task.apply()

        traces = self.pop_traces()
        assert len(traces) == 1
        span = traces[0][0]
        assert span.trace_id == 99999

    def test_distributed_tracing_propagation(self):
        @self.app.task
        def fn_task():
            return 42

        # This header manipulation is copying the work that should be done
        # by the before_publish signal. Rip it out if Celery ever fixes their bug.
        current_context = Pin.get_from(self.app).tracer.context_provider.active()
        headers = {}
        HTTPPropagator.inject(current_context, headers)

        with self.override_config("celery", dict(distributed_tracing=True)):
            fn_task.apply(headers=headers)

        traces = self.pop_traces()
        span = traces[0][0]
        assert span.trace_id == 12345

    def test_distributed_tracing_propagation_async(self):
        @self.app.task
        def fn_task():
            return 42

        # This header manipulation is copying the work that should be done
        # by the before_publish signal. Rip it out if Celery ever fixes their bug.
        current_context = Pin.get_from(self.app).tracer.context_provider.active()
        headers = {}
        HTTPPropagator.inject(current_context, headers)

        with self.override_config("celery", dict(distributed_tracing=True)):
            result = fn_task.apply_async(headers=headers)
            assert result.get(timeout=self.ASYNC_GET_TIMEOUT) == 42

        traces = self.pop_traces()

        if self.ASYNC_USE_CELERY_FIXTURES:
            assert 2 == len(traces)
            assert 1 == len(traces[0])
            assert 1 == len(traces[1])
            async_span = traces[0][0]
            run_span = traces[1][0]
            assert async_span.trace_id == 12345
        else:
            assert 1 == len(traces)
            assert 1 == len(traces[0])
            run_span = traces[0][0]

        assert run_span.trace_id == 12345

    def test_thread_start_during_fork(self):
        """Test that celery workers get spawned without problems.

        Starting threads while celery is forking worker processes is likely to
        causes a SIGSEGV with python<=3.6. With this test we enable the
        runtime metrics worker thread and ensure that celery worker processes
        are spawned without issues.
        """
        assert forksafe._soft

        with self.override_env(
            dict(
                DD_RUNTIME_METRICS_INTERVAL="2",
                DD_RUNTIME_METRICS_ENABLED="true",
            )
        ):
            celery = subprocess.Popen(
                ["ddtrace-run", "celery", "worker"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            sleep(5)
            celery.terminate()
            while True:
                err = celery.stdout.readline().strip()
                if not err:
                    break
                assert b"SIGSEGV" not in err
