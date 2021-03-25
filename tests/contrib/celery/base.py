from functools import wraps

import celery
import pytest

from ddtrace import Pin
from ddtrace.compat import PY2
from ddtrace.contrib.celery import patch
from ddtrace.contrib.celery import unpatch
from tests.utils import TracerTestCase

from ..config import REDIS_CONFIG


REDIS_URL = "redis://127.0.0.1:{port}".format(port=REDIS_CONFIG["port"])
BROKER_URL = "{redis}/{db}".format(redis=REDIS_URL, db=0)
BACKEND_URL = "{redis}/{db}".format(redis=REDIS_URL, db=1)


@pytest.fixture(scope="session")
def celery_config():
    return {"broker_url": BROKER_URL, "result_backend": BACKEND_URL}


@pytest.fixture
def celery_worker_parameters():
    return {
        # See https://github.com/celery/celery/issues/3642#issuecomment-457773294
        "perform_ping_check": False,
    }


class CeleryBaseTestCase(TracerTestCase):
    """Test case that handles a full fledged Celery application with a
    custom tracer. It patches the new Celery application.
    """

    # pytest fixtures needed for async execution only available for celery >= 4
    # celery 4.0 also avoided as it throws errors when using fixtures for async execution
    ASYNC_USE_CELERY_FIXTURES = celery.VERSION >= (4, 1)

    # set a high timeout for async executions due to issues in CI
    ASYNC_GET_TIMEOUT = 60

    @pytest.fixture(autouse=True)
    def instrument_celery(self):
        # instrument Celery and create an app with Broker and Result backends
        patch()
        yield
        # remove instrumentation from Celery
        unpatch()

    if ASYNC_USE_CELERY_FIXTURES:

        @pytest.fixture(autouse=True)
        def celery_test_setup(self, celery_app, celery_worker):
            # Patch task decorator on app fixture to reload worker
            # See https://github.com/celery/celery/issues/3642
            def wrap_task(fn):
                @wraps(fn)
                def wrapper(*args, **kwargs):
                    result = fn(*args, **kwargs)
                    celery_worker.reload()
                    return result

                return wrapper

            celery_app.task = wrap_task(celery_app.task)

            self.app = celery_app
            self.celery_worker = celery_worker

    else:

        @pytest.fixture(autouse=True)
        def celery_test_setup(self):
            self.app = celery.Celery("celery.test_app", broker=BROKER_URL, backend=BACKEND_URL)
            # lacking celery_worker fixture opt to simulate by using eager mode
            self.app.conf.CELERY_ALWAYS_EAGER = True
            return

    def setUp(self):
        super(CeleryBaseTestCase, self).setUp()

        self.pin = Pin(service="celery-unittest", tracer=self.tracer)
        # override pins to use our Dummy Tracer
        Pin.override(self.app, tracer=self.tracer)

    def tearDown(self):
        self.app = None

        super(CeleryBaseTestCase, self).tearDown()

    def assert_items_equal(self, a, b):
        if PY2:
            return self.assertItemsEqual(a, b)
        return self.assertCountEqual(a, b)
