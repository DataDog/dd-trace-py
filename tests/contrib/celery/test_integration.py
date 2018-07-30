import celery

from nose.tools import eq_, ok_

from .base import CeleryBaseTestCase


class CeleryIntegrationTask(CeleryBaseTestCase):
    """Ensures that the tracer works properly with a real Celery application
    without breaking the Application or Task API.
    """
    def test_concurrent_delays(self):
        # it should create one trace for each delayed execution
        @self.app.task
        def fn_task():
            return 42

        for x in range(100):
            fn_task.delay()

        traces = self.tracer.writer.pop_traces()
        eq_(100, len(traces))

    def test_fn_task_run(self):
        # the body of the function is not instrumented so calling it
        # directly doesn't create a trace
        @self.app.task
        def fn_task():
            return 42

        t = fn_task.run()
        eq_(t, 42)

        traces = self.tracer.writer.pop_traces()
        eq_(0, len(traces))

    def test_fn_task_call(self):
        # the body of the function is not instrumented so calling it
        # directly doesn't create a trace
        @self.app.task
        def fn_task():
            return 42

        t = fn_task()
        eq_(t, 42)

        traces = self.tracer.writer.pop_traces()
        eq_(0, len(traces))

    def test_fn_task_apply(self):
        # it should execute a traced task with a returning value
        @self.app.task
        def fn_task():
            return 42

        t = fn_task.apply()
        ok_(t.successful())
        eq_(42, t.result)

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        span = traces[0][0]
        eq_(span.error, 0)
        eq_(span.name, 'celery.run')
        eq_(span.resource, 'tests.contrib.celery.test_integration.fn_task')
        eq_(span.service, 'celery-worker')
        eq_(span.get_tag('celery.id'), t.task_id)
        eq_(span.get_tag('celery.action'), 'run')
        eq_(span.get_tag('celery.state'), 'SUCCESS')
        ok_(span.get_tag('celery.hostname') is not None)

    def test_fn_task_apply_bind(self):
        # it should execute a traced task with a returning value
        @self.app.task(bind=True)
        def fn_task(self):
            return self

        t = fn_task.apply()
        ok_(t.successful())
        ok_('fn_task' in t.result.name)

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        span = traces[0][0]
        eq_(span.error, 0)
        eq_(span.name, 'celery.run')
        eq_(span.resource, 'tests.contrib.celery.test_integration.fn_task')
        eq_(span.service, 'celery-worker')
        eq_(span.get_tag('celery.id'), t.task_id)
        eq_(span.get_tag('celery.action'), 'run')
        eq_(span.get_tag('celery.state'), 'SUCCESS')
        ok_(span.get_tag('celery.hostname') is not None)

    def test_fn_task_apply_async(self):
        # it should execute a traced async task that has parameters
        @self.app.task
        def fn_task_parameters(user, force_logout=False):
            return (user, force_logout)

        t = fn_task_parameters.apply_async(args=['user'], kwargs={'force_logout': True})
        eq_('PENDING', t.status)

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        span = traces[0][0]
        eq_(span.error, 0)
        eq_(span.name, 'celery.apply')
        eq_(span.resource, 'tests.contrib.celery.test_integration.fn_task_parameters')
        eq_(span.service, 'celery-producer')
        eq_(span.get_tag('celery.id'), t.task_id)
        eq_(span.get_tag('celery.action'), 'apply_async')
        eq_(span.get_tag('celery.routing_key'), 'celery')

    def test_fn_task_delay(self):
        # using delay shorthand must preserve arguments
        @self.app.task
        def fn_task_parameters(user, force_logout=False):
            return (user, force_logout)

        t = fn_task_parameters.delay('user', force_logout=True)
        eq_('PENDING', t.status)

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        span = traces[0][0]
        eq_(span.error, 0)
        eq_(span.name, 'celery.apply')
        eq_(span.resource, 'tests.contrib.celery.test_integration.fn_task_parameters')
        eq_(span.service, 'celery-producer')
        eq_(span.get_tag('celery.id'), t.task_id)
        eq_(span.get_tag('celery.action'), 'apply_async')
        eq_(span.get_tag('celery.routing_key'), 'celery')

    def test_fn_exception(self):
        # it should catch exceptions in task functions
        @self.app.task
        def fn_exception():
            raise Exception('Task class is failing')

        t = fn_exception.apply()
        ok_(t.failed())
        ok_('Task class is failing' in t.traceback)

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        span = traces[0][0]
        eq_(span.name, 'celery.run')
        eq_(span.resource, 'tests.contrib.celery.test_integration.fn_exception')
        eq_(span.service, 'celery-worker')
        eq_(span.get_tag('celery.id'), t.task_id)
        eq_(span.get_tag('celery.action'), 'run')
        eq_(span.get_tag('celery.state'), 'FAILURE')
        ok_(span.get_tag('celery.hostname') is not None)
        eq_(span.error, 1)
        eq_(span.get_tag('error.msg'), 'Task class is failing')
        ok_('Traceback (most recent call last)' in span.get_tag('error.stack'))
        ok_('Task class is failing' in span.get_tag('error.stack'))

    def test_class_task(self):
        # it should execute class based tasks with a returning value
        class BaseTask(self.app.Task):
            def run(self):
                return 42

        t = BaseTask()
        # register the Task class if it's available (required in Celery 4.0+)
        register_task = getattr(self.app, 'register_task', None)
        if register_task is not None:
            register_task(t)

        r = t.apply()
        ok_(r.successful())
        eq_(42, r.result)

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        span = traces[0][0]
        eq_(span.error, 0)
        eq_(span.name, 'celery.run')
        eq_(span.resource, 'tests.contrib.celery.test_integration.BaseTask')
        eq_(span.service, 'celery-worker')
        eq_(span.get_tag('celery.id'), r.task_id)
        eq_(span.get_tag('celery.action'), 'run')
        eq_(span.get_tag('celery.state'), 'SUCCESS')
        ok_(span.get_tag('celery.hostname') is not None)

    def test_class_task_exception(self):
        # it should catch exceptions in class based tasks
        class BaseTask(self.app.Task):
            def run(self):
                raise Exception('Task class is failing')

        t = BaseTask()
        # register the Task class if it's available (required in Celery 4.0+)
        register_task = getattr(self.app, 'register_task', None)
        if register_task is not None:
            register_task(t)

        r = t.apply()
        ok_(r.failed())
        ok_('Task class is failing' in r.traceback)

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        span = traces[0][0]
        eq_(span.name, 'celery.run')
        eq_(span.resource, 'tests.contrib.celery.test_integration.BaseTask')
        eq_(span.service, 'celery-worker')
        eq_(span.get_tag('celery.id'), r.task_id)
        eq_(span.get_tag('celery.action'), 'run')
        eq_(span.get_tag('celery.state'), 'FAILURE')
        ok_(span.get_tag('celery.hostname') is not None)
        eq_(span.error, 1)
        eq_(span.get_tag('error.msg'), 'Task class is failing')
        ok_('Traceback (most recent call last)' in span.get_tag('error.stack'))
        ok_('Task class is failing' in span.get_tag('error.stack'))

    def test_shared_task(self):
        # Ensure Django Shared Task are supported
        @celery.shared_task
        def add(x ,y):
            return x + y

        res = add.apply([2, 2])
        eq_(res.result, 4)

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        span = traces[0][0]
        eq_(span.error, 0)
        eq_(span.name, 'celery.run')
        eq_(span.service, 'celery-worker')
        eq_(span.resource, 'tests.contrib.celery.test_integration.add')
        ok_(span.parent_id is None)
        eq_(span.get_tag('celery.id'), res.task_id)
        eq_(span.get_tag('celery.action'), 'run')
        eq_(span.get_tag('celery.state'), 'SUCCESS')
        ok_(span.get_tag('celery.hostname') is not None)
