import celery

from .base import CeleryBaseTestCase
from .utils import patch_task_with_pin


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

        @patch_task_with_pin(pin=self.pin)
        class CelerySubClass(CelerySuperClass):
            pass

        t = CelerySubClass()
        t.run()
        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 2)
