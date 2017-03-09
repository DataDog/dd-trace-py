from nose.tools import eq_, ok_

from .utils import CeleryTestCase


class CeleryIntegrationTask(CeleryTestCase):
    """
    Ensures that the tracer works properly with a real Celery application
    without breaking the Application or Task APIs.
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

    def test_fn_task(self):
        # it should execute a traced task with a returning value
        @self.app.task
        def fn_task():
            return 42

        t = fn_task.apply()
        ok_(t.successful())
        eq_(42, t.result)

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(2, len(traces[0]))
        eq_('celery.task.apply', traces[0][0].name)
        eq_('celery.task.run', traces[0][1].name)
        eq_('tests.contrib.celery.test_integration.fn_task', traces[0][0].resource)
        eq_('tests.contrib.celery.test_integration.fn_task', traces[0][1].resource)
        eq_('celery', traces[0][0].service)
        eq_('celery', traces[0][1].service)
        eq_('SUCCESS', traces[0][0].get_tag('state'))

    def test_fn_task_bind(self):
        # it should execute a traced task with a returning value
        @self.app.task(bind=True)
        def fn_task(self):
            return self

        t = fn_task.apply()
        ok_(t.successful())
        ok_('fn_task' in t.result.name)

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(2, len(traces[0]))
        eq_('celery.task.apply', traces[0][0].name)
        eq_('celery.task.run', traces[0][1].name)
        eq_('tests.contrib.celery.test_integration.fn_task', traces[0][0].resource)
        eq_('tests.contrib.celery.test_integration.fn_task', traces[0][1].resource)
        eq_('celery', traces[0][0].service)
        eq_('celery', traces[0][1].service)
        eq_('SUCCESS', traces[0][0].get_tag('state'))

    def test_fn_task_parameters(self):
        # it should execute a traced task that has parameters
        @self.app.task
        def fn_task_parameters(user, force_logout=False):
            return (user, force_logout)

        t = fn_task_parameters.apply(args=['user'], kwargs={'force_logout': True})
        ok_(t.successful())
        eq_('user', t.result[0])
        ok_(t.result[1] is True)

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(2, len(traces[0]))
        eq_('celery.task.apply', traces[0][0].name)
        eq_('celery.task.run', traces[0][1].name)
        eq_('tests.contrib.celery.test_integration.fn_task_parameters', traces[0][0].resource)
        eq_('tests.contrib.celery.test_integration.fn_task_parameters', traces[0][1].resource)
        eq_('celery', traces[0][0].service)
        eq_('celery', traces[0][1].service)
        eq_('SUCCESS', traces[0][0].get_tag('state'))

    def test_fn_task_parameters_bind(self):
        # it should execute a traced task that has parameters
        @self.app.task(bind=True)
        def fn_task_parameters(self, user, force_logout=False):
            return (self, user, force_logout)

        t = fn_task_parameters.apply(args=['user'], kwargs={'force_logout': True})
        ok_(t.successful())
        ok_('fn_task_parameters' in t.result[0].name)
        eq_('user', t.result[1])
        ok_(t.result[2] is True)

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(2, len(traces[0]))
        eq_('celery.task.apply', traces[0][0].name)
        eq_('celery.task.run', traces[0][1].name)
        eq_('tests.contrib.celery.test_integration.fn_task_parameters', traces[0][0].resource)
        eq_('tests.contrib.celery.test_integration.fn_task_parameters', traces[0][1].resource)
        eq_('celery', traces[0][0].service)
        eq_('celery', traces[0][1].service)
        eq_('SUCCESS', traces[0][0].get_tag('state'))

    def test_fn_task_parameters_async(self):
        # it should execute a traced async task that has parameters
        @self.app.task
        def fn_task_parameters(user, force_logout=False):
            return (user, force_logout)

        t = fn_task_parameters.apply_async(args=['user'], kwargs={'force_logout': True})
        eq_('PENDING', t.status)

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        eq_('celery.task.apply_async', traces[0][0].name)
        eq_('tests.contrib.celery.test_integration.fn_task_parameters', traces[0][0].resource)
        eq_('celery', traces[0][0].service)
        ok_(traces[0][0].get_tag('id') is not None)

    def test_fn_task_parameters_delay(self):
        # using delay shorthand must preserve arguments
        @self.app.task
        def fn_task_parameters(user, force_logout=False):
            return (user, force_logout)

        t = fn_task_parameters.delay('user', force_logout=True)
        eq_('PENDING', t.status)

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(1, len(traces[0]))
        eq_('celery.task.apply_async', traces[0][0].name)
        eq_('tests.contrib.celery.test_integration.fn_task_parameters', traces[0][0].resource)
        eq_('celery', traces[0][0].service)
        ok_(traces[0][0].get_tag('id') is not None)

    def test_fn_exception(self):
        # it should catch exceptions in task functions
        @self.app.task
        def fn_exception():
            raise Exception('Task class is failing')

        r = fn_exception.apply()
        ok_(r.failed())
        ok_('Task class is failing' in r.traceback)

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(2, len(traces[0]))
        eq_('celery.task.apply', traces[0][0].name)
        eq_('celery.task.run', traces[0][1].name)
        eq_('tests.contrib.celery.test_integration.fn_exception', traces[0][0].resource)
        eq_('tests.contrib.celery.test_integration.fn_exception', traces[0][1].resource)
        eq_('celery', traces[0][0].service)
        eq_('celery', traces[0][1].service)
        eq_('FAILURE', traces[0][0].get_tag('state'))
        eq_(1, traces[0][1].error)
        eq_('Task class is failing', traces[0][1].get_tag('error.msg'))
        ok_('Traceback (most recent call last)' in traces[0][1].get_tag('error.stack'))
        ok_('Task class is failing' in traces[0][1].get_tag('error.stack'))

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
        eq_(2, len(traces[0]))
        eq_('celery.task.apply', traces[0][0].name)
        eq_('celery.task.run', traces[0][1].name)
        eq_('tests.contrib.celery.test_integration.BaseTask', traces[0][0].resource)
        eq_('tests.contrib.celery.test_integration.BaseTask', traces[0][1].resource)
        eq_('celery', traces[0][0].service)
        eq_('celery', traces[0][1].service)
        eq_('SUCCESS', traces[0][0].get_tag('state'))

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
        eq_(2, len(traces[0]))
        eq_('celery.task.apply', traces[0][0].name)
        eq_('celery.task.run', traces[0][1].name)
        eq_('tests.contrib.celery.test_integration.BaseTask', traces[0][0].resource)
        eq_('tests.contrib.celery.test_integration.BaseTask', traces[0][1].resource)
        eq_('celery', traces[0][0].service)
        eq_('celery', traces[0][1].service)
        eq_('FAILURE', traces[0][0].get_tag('state'))
        eq_(1, traces[0][1].error)
        eq_('Task class is failing', traces[0][1].get_tag('error.msg'))
        ok_('Traceback (most recent call last)' in traces[0][1].get_tag('error.stack'))
        ok_('Task class is failing' in traces[0][1].get_tag('error.stack'))
