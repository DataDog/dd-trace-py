import unittest

import celery
import wrapt

from ddtrace.contrib.celery.app import patch_app, unpatch_app


class CeleryAppTest(unittest.TestCase):
    def setUp(self):
        patch_app(celery.Celery)

    def tearDown(self):
        unpatch_app(celery.Celery)

    def test_patch_app(self):
        """
        When celery.App is patched
            the task() method will return a patched task
        """
        # Assert the base class has the wrapped function
        self.assertIsInstance(celery.Celery.task, wrapt.BoundFunctionWrapper)

        # Create an instance of `celery.Celery`
        app = celery.Celery()

        # Assert the instance method is the wrapped function
        self.assertIsInstance(app.task, wrapt.BoundFunctionWrapper)

    def test_unpatch_app(self):
        """
        When unpatch_app is called on a patched app
            we unpatch the `task()` method
        """
        # Assert it is patched before we start
        self.assertIsInstance(celery.Celery.task, wrapt.BoundFunctionWrapper)

        # Unpatch the app
        unpatch_app(celery.Celery)

        # Assert the method is not patched
        self.assertFalse(isinstance(celery.Celery.task, wrapt.BoundFunctionWrapper))
