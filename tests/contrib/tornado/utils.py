from tornado.testing import AsyncHTTPTestCase

from ddtrace.contrib.tornado import patch
from ddtrace.contrib.tornado import unpatch
from ddtrace.contrib.futures import patch as patch_futures
from ddtrace.contrib.futures import unpatch as unpatch_futures
from ddtrace.internal.compat import reload_module
from tests.utils import TracerTestCase

from .web import app
from .web import compat


class TornadoTestCase(TracerTestCase, AsyncHTTPTestCase):
    """
    Generic TornadoTestCase where the framework is globally patched
    and unpatched before/after each test. A dummy tracer is provided
    in the `self.tracer` attribute.
    """

    def get_app(self):
        # patch Tornado and reload module app
        patch()
        patch_futures()
        reload_module(compat)
        reload_module(app)

        settings = self.get_settings()
        trace_settings = settings.get("datadog_trace", {})
        settings["datadog_trace"] = trace_settings
        trace_settings["tracer"] = self.tracer
        self.app = app.make_app(settings=settings)
        return self.app

    def get_settings(self):
        # override settings in your TestCase
        return {}

    def tearDown(self):
        super(TornadoTestCase, self).tearDown()
        # unpatch Tornado
        unpatch()
        unpatch_futures()
        reload_module(compat)
        reload_module(app)
