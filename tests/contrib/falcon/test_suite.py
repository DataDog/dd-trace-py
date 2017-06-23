from nose.tools import eq_, ok_

from ddtrace.ext import errors as errx, http as httpx


class FalconTestCase(object):
    """Falcon mixin test case that includes all possible tests. If you need
    to add new tests, add them here so that they're shared across manual
    and automatic instrumentation.
    """
    def test_falcon_service(self):
        services = self.tracer.writer.pop_services()
        expected_service = {
            'app_type': 'web',
            'app': 'falcon',
        }
        # ensure users set service name is in the services list
        eq_(self._service, services.keys())
        eq_(services['falcon'], expected_service)

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

    def test_exception(self):
        try:
            self.simulate_get('/exception')
        except Exception:
            pass
        else:
            assert 0

        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)
        span = traces[0][0]
        eq_(span.name, 'falcon.request')
        eq_(span.service, self._service)
        eq_(span.resource, 'GET tests.contrib.falcon.app.resources.ResourceException')
        eq_(span.get_tag(httpx.STATUS_CODE), '500')
        eq_(span.get_tag(httpx.URL), 'http://falconframework.org/exception')

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
