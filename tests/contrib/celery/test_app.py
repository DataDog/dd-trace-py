import celery
import wrapt

from ddtrace.contrib.celery import unpatch_app

from .base import CeleryBaseTestCase


class CeleryAppTest(CeleryBaseTestCase):
    """Ensures the default application is properly instrumented"""

    def test_patch_app(self):
        # When celery.App is patched the task() method will return a patched task
        app = celery.Celery()
        self.assertIsInstance(celery.Celery.task, wrapt.BoundFunctionWrapper)
        self.assertIsInstance(app.task, wrapt.BoundFunctionWrapper)

    def test_unpatch_app(self):
        # When unpatch_app is called on a patched app we unpatch the `task()` method
        unpatch_app(celery.Celery)
        app = celery.Celery()
        self.assertFalse(isinstance(celery.Celery.task, wrapt.BoundFunctionWrapper))
        self.assertFalse(isinstance(app.task, wrapt.BoundFunctionWrapper))
