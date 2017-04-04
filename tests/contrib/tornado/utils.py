from tornado.testing import AsyncHTTPTestCase

from ddtrace.contrib.tornado import patch, unpatch

from . import web
from ...test_tracer import get_dummy_tracer


class TornadoTestCase(AsyncHTTPTestCase):
    """
    Generic TornadoTestCase where the framework is globally patched
    and unpatched before/after each test. A dummy tracer is provided
    in the `self.tracer` attribute.
    """
    def get_app(self):
        # patch Tornado
        patch()
        # create a dummy tracer and a Tornado web application
        self.tracer = get_dummy_tracer()
        settings = {
            'datadog_trace': {
                'tracer': self.tracer,
            },
        }

        settings.update(self.get_settings())
        self.app = web.make_app(settings=settings)
        return self.app

    def get_settings(self):
        # override settings in your TestCase
        return {}

    def tearDown(self):
        super(TornadoTestCase, self).tearDown()
        # unpatch Tornado
        unpatch()
