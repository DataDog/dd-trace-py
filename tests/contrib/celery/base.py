import unittest

from celery import Celery

from ddtrace import Pin, config
from ddtrace.compat import PY2
from ddtrace.contrib.celery import patch, unpatch

from ..config import REDIS_CONFIG
from ...test_tracer import get_dummy_tracer


REDIS_URL = 'redis://127.0.0.1:{port}'.format(port=REDIS_CONFIG['port'])
BROKER_URL = '{redis}/{db}'.format(redis=REDIS_URL, db=0)
BACKEND_URL = '{redis}/{db}'.format(redis=REDIS_URL, db=1)


class CeleryBaseTestCase(unittest.TestCase):
    """Test case that handles a full fledged Celery application with a
    custom tracer. It patches the new Celery application.
    """

    def setUp(self):
        # keep track of original config
        self._config = dict(config.celery)
        # instrument Celery and create an app with Broker and Result backends
        patch()
        self.tracer = get_dummy_tracer()
        self.pin = Pin(service='celery-unittest', tracer=self.tracer)
        self.app = Celery('celery.test_app', broker=BROKER_URL, backend=BACKEND_URL)
        # override pins to use our Dummy Tracer
        Pin.override(self.app, tracer=self.tracer)

    def tearDown(self):
        # remove instrumentation from Celery
        unpatch()
        self.app = None
        # restore the global configuration
        config.celery.update(self._config)
        self._config = None

    def assert_items_equal(self, a, b):
        if PY2:
            return self.assertItemsEqual(a, b)
        return self.assertCountEqual(a, b)
