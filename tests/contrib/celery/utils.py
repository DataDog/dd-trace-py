import ddtrace

import wrapt
from unittest import TestCase
from celery import Celery

from ddtrace.contrib.celery import patch_app, patch_task

from ..config import REDIS_CONFIG
from ...test_tracer import get_dummy_tracer


REDIS_URL = 'redis://127.0.0.1:{port}'.format(port=REDIS_CONFIG['port'])
BROKER_URL = '{redis}/{db}'.format(redis=REDIS_URL, db=0)
BACKEND_URL = '{redis}/{db}'.format(redis=REDIS_URL, db=1)


class CeleryTestCase(TestCase):
    """
    Test case that handles a full fledged Celery application
    with a custom tracer. It automatically patches the new
    Celery application.
    """
    def setUp(self):
        # use a dummy tracer
        self.tracer = get_dummy_tracer()
        self._original_tracer = ddtrace.tracer
        ddtrace.tracer = self.tracer
        # create and patch a new application
        self.app = patch_app(Celery('celery.test_app', broker=BROKER_URL, backend=BACKEND_URL))

def patch_task_with_pin(pin=None):
    """ patch_task_with_pin can be used as a decorator for v1 Celery tasks when specifying a pin is needed"""
    @wrapt.decorator
    def wrapper(wrapped, instance, args, kwargs):
        patch_task(wrapped, pin)
        return wrapped(*args, **kwargs)
    return wrapper
