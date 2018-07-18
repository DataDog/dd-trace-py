import unittest

import celery

from ddtrace import Pin
from ddtrace.compat import PY2
from ddtrace.contrib.celery.app import patch_app, unpatch_app
from ddtrace.contrib.celery.task import patch_task, unpatch_task

from ..config import REDIS_CONFIG
from ...test_tracer import get_dummy_tracer


class CeleryBaseTestCase(unittest.TestCase):
    """Celery base test class. It patches Celery main class and task before
    initializing a Celery app.
    """

    def setUp(self):
        self.broker_url = 'redis://127.0.0.1:{port}/0'.format(port=REDIS_CONFIG['port'])
        self.tracer = get_dummy_tracer()
        self.pin = Pin(service='celery-ignored', tracer=self.tracer)
        patch_app(celery.Celery, pin=self.pin)
        patch_task(celery.Task, pin=self.pin)

    def tearDown(self):
        unpatch_app(celery.Celery)
        unpatch_task(celery.Task)

    def assert_items_equal(self, a, b):
        if PY2:
            return self.assertItemsEqual(a, b)
        return self.assertCountEqual(a, b)
