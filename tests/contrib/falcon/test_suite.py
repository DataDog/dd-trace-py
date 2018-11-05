from nose.tools import eq_, ok_, assert_raises

from ddtrace.ext import errors as errx, http as httpx
from tests.opentracer.utils import init_tracer

from .app import resources


class FalconTestCase(object):
    """Falcon mixin test case that includes all possible tests. If you need
    to add new tests, add them here so that they're shared across manual
    and automatic instrumentation.
    """
    def test_falcon_service(self):
        services = self.tracer._services
        expected_service = (self._service, 'falcon', 'web')

        # ensure users set service name is in the services list
        ok_(self._service in services.keys())
        eq_(services[self._service], expected_service)

    def test_double_instrument(self):
        # Ensure that when a user instruments manually AND we patch that we
        # are not duplicating our instrumentation.

        # falcon uses the global tracer internally so replace it with our test
        # tracer
        import ddtrace
        ddtrace.tracer = self.tracer
        from ddtrace.contrib.falcon import TraceMiddleware, patch

        # patch will patch the __init__ of falcon.API to install middleware
        patch()
        import falcon

        # include the trace middleware ourselves on top of the default patching
        app = falcon.API(middleware=[
            TraceMiddleware(self.tracer, service=self._service),
        ])
        app.add_route('/200', resources.Resource200())

        # very similar test behaviour to self.test_200()
        out = self.simulate_get('/200')
        eq_(out.status_code, 200)
        eq_(out.content.decode('utf-8'), 'Success')

        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)
        span = traces[0][0]
        eq_(span.name, 'falcon.request')
        eq_(span.service, self._service)
        eq_(span.resource, 'GET tests.contrib.falcon.app.resources.Resource200')
        eq_(span.get_tag(httpx.STATUS_CODE), '200')
        eq_(span.get_tag(httpx.URL), 'http://falconframework.org/200')
        eq_(span.parent_id, None)
        eq_(span.span_type, 'http')

    def test_404(self):
        out = self.simulate_get('/fake_endpoint')
        eq_(out.status_code, 404)

        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)
        span = traces[0][0]
        eq_(span.name, 'falcon.request')
        eq_(span.service, self._service)
        eq_(span.resource, 'GET 404')
        eq_(span.get_tag(httpx.STATUS_CODE), '404')
        eq_(span.get_tag(httpx.URL), 'http://falconframework.org/fake_endpoint')
        eq_(span.parent_id, None)

    def test_exception(self):
        with assert_raises(Exception):
            self.simulate_get('/exception')

        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)
        span = traces[0][0]
        eq_(span.name, 'falcon.request')
        eq_(span.service, self._service)
        eq_(span.resource, 'GET tests.contrib.falcon.app.resources.ResourceException')
        eq_(span.get_tag(httpx.STATUS_CODE), '500')
        eq_(span.get_tag(httpx.URL), 'http://falconframework.org/exception')
        eq_(span.parent_id, None)

    def test_200(self):
        out = self.simulate_get('/200')
        eq_(out.status_code, 200)
        eq_(out.content.decode('utf-8'), 'Success')

        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)
        span = traces[0][0]
        eq_(span.name, 'falcon.request')
        eq_(span.service, self._service)
        eq_(span.resource, 'GET tests.contrib.falcon.app.resources.Resource200')
        eq_(span.get_tag(httpx.STATUS_CODE), '200')
        eq_(span.get_tag(httpx.URL), 'http://falconframework.org/200')
        eq_(span.parent_id, None)
        eq_(span.span_type, 'http')

    def test_201(self):
        out = self.simulate_post('/201')
        eq_(out.status_code, 201)
        eq_(out.content.decode('utf-8'), 'Success')

        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)
        span = traces[0][0]
        eq_(span.name, 'falcon.request')
        eq_(span.service, self._service)
        eq_(span.resource, 'POST tests.contrib.falcon.app.resources.Resource201')
        eq_(span.get_tag(httpx.STATUS_CODE), '201')
        eq_(span.get_tag(httpx.URL), 'http://falconframework.org/201')
        eq_(span.parent_id, None)

    def test_500(self):
        out = self.simulate_get('/500')
        eq_(out.status_code, 500)
        eq_(out.content.decode('utf-8'), 'Failure')

        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)
        span = traces[0][0]
        eq_(span.name, 'falcon.request')
        eq_(span.service, self._service)
        eq_(span.resource, 'GET tests.contrib.falcon.app.resources.Resource500')
        eq_(span.get_tag(httpx.STATUS_CODE), '500')
        eq_(span.get_tag(httpx.URL), 'http://falconframework.org/500')
        eq_(span.parent_id, None)

    def test_404_exception(self):
        out = self.simulate_get('/not_found')
        eq_(out.status_code, 404)

        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)
        span = traces[0][0]
        eq_(span.name, 'falcon.request')
        eq_(span.service, self._service)
        eq_(span.resource, 'GET tests.contrib.falcon.app.resources.ResourceNotFound')
        eq_(span.get_tag(httpx.STATUS_CODE), '404')
        eq_(span.get_tag(httpx.URL), 'http://falconframework.org/not_found')
        eq_(span.parent_id, None)

    def test_404_exception_no_stacktracer(self):
        # it should not have the stacktrace when a 404 exception is raised
        out = self.simulate_get('/not_found')
        eq_(out.status_code, 404)

        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)
        span = traces[0][0]
        eq_(span.name, 'falcon.request')
        eq_(span.service, self._service)
        eq_(span.get_tag(httpx.STATUS_CODE), '404')
        ok_(span.get_tag(errx.ERROR_TYPE) is None)
        eq_(span.parent_id, None)

    def test_200_ot(self):
        """OpenTracing version of test_200."""
        ot_tracer = init_tracer('my_svc', self.tracer)

        with ot_tracer.start_active_span('ot_span'):
            out = self.simulate_get('/200')

        eq_(out.status_code, 200)
        eq_(out.content.decode('utf-8'), 'Success')

        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 1)
        eq_(len(traces[0]), 2)
        ot_span, dd_span = traces[0]

        # confirm the parenting
        eq_(ot_span.parent_id, None)
        eq_(dd_span.parent_id, ot_span.span_id)

        eq_(ot_span.service, 'my_svc')
        eq_(ot_span.resource, 'ot_span')

        eq_(dd_span.name, 'falcon.request')
        eq_(dd_span.service, self._service)
        eq_(dd_span.resource, 'GET tests.contrib.falcon.app.resources.Resource200')
        eq_(dd_span.get_tag(httpx.STATUS_CODE), '200')
        eq_(dd_span.get_tag(httpx.URL), 'http://falconframework.org/200')
