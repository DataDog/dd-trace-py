import celery

from nose.tools import eq_

from .base import CeleryBaseTestCase


class CeleryOldStyleTaskTest(CeleryBaseTestCase):
    """Ensure Old Style Tasks are properly instrumented"""

    def test_apply_async_previous_style_tasks(self):
        # ensures apply_async is properly patched if Celery 1.0 style tasks
        # are used even in newer versions. This should extend support to
        # previous versions of Celery.
        # Regression test: https://github.com/DataDog/dd-trace-py/pull/449
        class CelerySuperClass(celery.task.Task):
            abstract = True

            @classmethod
            def apply_async(cls, args=None, kwargs=None, **kwargs_):
                return super(CelerySuperClass, cls).apply_async(args=args, kwargs=kwargs, **kwargs_)

            def run(self, *args, **kwargs):
                if 'stop' in kwargs:
                    # avoid call loop
                    return
                CelerySubClass.apply_async(args=[], kwargs={"stop": True})

        class CelerySubClass(CelerySuperClass):
            pass

        t = CelerySubClass()
        res = t.apply()

        traces = self.tracer.writer.pop_traces()
        eq_(1, len(traces))
        eq_(2, len(traces[0]))
        run_span = traces[0][0]
        eq_(run_span.error, 0)
        eq_(run_span.name, 'celery.run')
        eq_(run_span.resource, 'tests.contrib.celery.test_old_style_task.CelerySubClass')
        eq_(run_span.service, 'celery-worker')
        eq_(run_span.get_tag('celery.id'), res.task_id)
        eq_(run_span.get_tag('celery.action'), 'run')
        eq_(run_span.get_tag('celery.state'), 'SUCCESS')
        apply_span = traces[0][1]
        eq_(apply_span.error, 0)
        eq_(apply_span.name, 'celery.apply')
        eq_(apply_span.resource, 'tests.contrib.celery.test_old_style_task.CelerySubClass')
        eq_(apply_span.service, 'celery-producer')
        eq_(apply_span.get_tag('celery.action'), 'apply_async')
        eq_(apply_span.get_tag('celery.routing_key'), 'celery')
