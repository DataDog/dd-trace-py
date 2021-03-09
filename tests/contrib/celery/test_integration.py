import celery
from celery.exceptions import Retry
import pytest

from ddtrace import Pin
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.context import Context
from ddtrace.contrib.celery import patch
from ddtrace.contrib.celery import unpatch
from ddtrace.propagation.http import HTTPPropagator
from tests.opentracer.utils import init_tracer

from .base import CeleryBaseTestCase


class MyException(Exception):
    pass


class CeleryIntegrationTask(CeleryBaseTestCase):
    """Ensures that the tracer works properly with a real Celery application
    without breaking the Application or Task API.
    """

    def test_concurrent_delays(self):
        # it should create one trace for each delayed execution
        @self.app.task
        def fn_task():
            return 42

        results = [fn_task.delay() for _ in range(100)]

        for result in results:
            assert result.get(timeout=self.ASYNC_GET_TIMEOUT) == 42

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
        assert span.get_tag("error.msg") == "Task class is failing"
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

        # This type of retrying should not be marked as an exception
        assert span.error == 0
        assert not span.get_tag("error.msg")
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
        assert span.get_tag("error.msg") == "Task class is failing"
        assert "Traceback (most recent call last)" in span.get_tag("error.stack")
        assert "Task class is failing" in span.get_tag("error.stack")

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
        assert span.error == 0

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

    def test_worker_analytics_default(self):
        @self.app.task
        def fn_task():
            return 42

        # Ensure worker analytics sample rate is disabled by default
        t = fn_task.apply()
        self.assertTrue(t.successful())
        self.assertEqual(42, t.result)

        traces = self.pop_traces()
        self.assertEqual(1, len(traces))
        self.assertEqual(1, len(traces[0]))
        span = traces[0][0]
        self.assertIsNone(span.get_metric(ANALYTICS_SAMPLE_RATE_KEY))

    def test_worker_analytics_with_rate(self):
        @self.app.task
        def fn_task():
            return 42

        # Ensure worker analytics sample rate can be changed via
        # configuration object
        with self.override_config("celery", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
            t = fn_task.apply()
            self.assertTrue(t.successful())
            self.assertEqual(42, t.result)

            traces = self.pop_traces()
            self.assertEqual(1, len(traces))
            self.assertEqual(1, len(traces[0]))
            span = traces[0][0]
            self.assertEqual(span.get_metric(ANALYTICS_SAMPLE_RATE_KEY), 0.5)

    def test_worker_analytics_without_rate(self):
        @self.app.task
        def fn_task():
            return 42

        # Ensure worker analytics is 1.0 by default when enabled
        # configuration object
        with self.override_config("celery", dict(analytics_enabled=True)):
            t = fn_task.apply()
            self.assertTrue(t.successful())
            self.assertEqual(42, t.result)

            traces = self.pop_traces()
            self.assertEqual(1, len(traces))
            self.assertEqual(1, len(traces[0]))
            span = traces[0][0]
            self.assertEqual(span.get_metric(ANALYTICS_SAMPLE_RATE_KEY), 1.0)

    def test_producer_analytics_default(self):
        @self.app.task
        def fn_task():
            return 42

        # Ensure producer analytics sample rate is disabled by default
        t = fn_task.delay()
        assert t.get(timeout=self.ASYNC_GET_TIMEOUT) == 42

        traces = self.pop_traces()

        if self.ASYNC_USE_CELERY_FIXTURES:
            self.assertEqual(2, len(traces))
        else:
            self.assertEqual(1, len(traces))

        for trace in traces:
            self.assertEqual(1, len(trace))
            self.assertIsNone(trace[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY))

    def test_producer_analytics_with_rate(self):
        @self.app.task
        def fn_task():
            return 42

        # Ensure producer analytics sample rate can be changed via
        # configuration object
        with self.override_config("celery", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
            t = fn_task.delay()
            assert t.get(timeout=self.ASYNC_GET_TIMEOUT) == 42

            traces = self.pop_traces()

            if self.ASYNC_USE_CELERY_FIXTURES:
                self.assertEqual(2, len(traces))
            else:
                self.assertEqual(1, len(traces))

            for trace in traces:
                self.assertEqual(1, len(trace))
                self.assertEqual(trace[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY), 0.5)

    def test_producer_analytics_without_rate(self):
        @self.app.task
        def fn_task():
            return 42

        # Ensure producer analytics is 1.0 by default when enabled
        # configuration object
        with self.override_config("celery", dict(analytics_enabled=True)):
            t = fn_task.delay()
            assert t.get(timeout=self.ASYNC_GET_TIMEOUT) == 42

            traces = self.pop_traces()

            if self.ASYNC_USE_CELERY_FIXTURES:
                self.assertEqual(2, len(traces))
            else:
                self.assertEqual(1, len(traces))

            for trace in traces:
                self.assertEqual(1, len(trace))
                self.assertEqual(trace[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY), 1.0)

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

        traces = self.pop_traces()

        if self.ASYNC_USE_CELERY_FIXTURES:
            assert 2 == len(traces)
            assert 1 == len(traces[0])
            assert 2 == len(traces[1])
            run_span = traces[0][0]
            ot_span, async_span = traces[1]

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
        else:
            assert 1 == len(traces)
            assert 2 == len(traces[0])
            ot_span, run_span = traces[0]

        assert ot_span.parent_id is None
        assert ot_span.name == "celery_op"
        assert ot_span.service == "celery_svc"

        assert run_span.name == "celery.run"
        assert run_span.resource == "tests.contrib.celery.test_integration.fn_task_parameters"
        assert run_span.service == "celery-worker"
        assert run_span.get_tag("celery.id") == t.task_id
        assert run_span.get_tag("celery.action") == "run"


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
