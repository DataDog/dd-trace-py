"""
test for falcon. run this module with python to run the test web server.
"""

# stdlib
from wsgiref import simple_server

# 3p
import falcon
import falcon.testing
from nose.tools import eq_, ok_
from nose.plugins.attrib import attr

# project
from ddtrace import tracer
from ddtrace.contrib.falcon import TraceMiddleware
from ddtrace.ext import http as httpx
from tests.test_tracer import DummyWriter


class Resource200(object):

    BODY = "yaasss"
    ROUTE = "/200"

    def on_get(self, req, resp, **kwargs):

        # throw a handled exception here to ensure our use of
        # set_traceback doesn't affect 200s
        try:
            1/0
        except Exception:
            pass

        resp.status = falcon.HTTP_200
        resp.body = self.BODY


class Resource500(object):

    BODY = "noo"
    ROUTE = "/500"

    def on_get(self, req, resp, **kwargs):
        resp.status = falcon.HTTP_500
        resp.body = self.BODY


class ResourceExc(object):

    ROUTE = "/exc"

    def on_get(self, req, resp, **kwargs):
        raise Exception("argh")


class TestMiddleware(falcon.testing.TestCase):

    def setUp(self):
        self._tracer = tracer
        self._writer = DummyWriter()
        self._tracer.writer = self._writer
        self._service = "my-falcon"

        self.api = falcon.API()

        resources = [
            Resource200,
            Resource500,
            ResourceExc,
        ]
        for r in resources:
            self.api.add_route(r.ROUTE, r())

    def test_autopatched(self):
        ok_(falcon._datadog_patch)

    @attr('404')
    def test_404(self):
        out = self.simulate_get('/404')
        eq_(out.status_code, 404)

        spans = self._writer.pop()
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self._service)
        eq_(span.resource, "GET 404")
        eq_(span.get_tag(httpx.STATUS_CODE), '404')
        eq_(span.name, "falcon.request")


    def test_exception(self):
        try:
            self.simulate_get(ResourceExc.ROUTE)
        except Exception:
            pass
        else:
            assert 0

        spans = self._writer.pop()
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self._service)
        eq_(span.resource, "GET tests.contrib.falcon.test_autopatch.ResourceExc")
        eq_(span.get_tag(httpx.STATUS_CODE), '500')
        eq_(span.name, "falcon.request")

    def test_200(self):
        out = self.simulate_get(Resource200.ROUTE)
        eq_(out.status_code, 200)
        eq_(out.content.decode('utf-8'), Resource200.BODY)

        spans = self._writer.pop()
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self._service)
        eq_(span.resource, "GET tests.contrib.falcon.test_autopatch.Resource200")
        eq_(span.get_tag(httpx.STATUS_CODE), '200')
        eq_(span.name, "falcon.request")

    def test_500(self):
        out = self.simulate_get(Resource500.ROUTE)
        eq_(out.status_code, 500)
        eq_(out.content.decode('utf-8'), Resource500.BODY)

        spans = self._writer.pop()
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self._service)
        eq_(span.resource, "GET tests.contrib.falcon.test_autopatch.Resource500")
        eq_(span.get_tag(httpx.STATUS_CODE), '500')
        eq_(span.name, "falcon.request")


if __name__ == '__main__':
    app = falcon.API()

    resources = [
        Resource200,
        Resource500,
        ResourceExc,
    ]
    for r in resources:
        app.add_route(r.ROUTE, r())

    port = 8000
    httpd = simple_server.make_server('127.0.0.1', port, app)
    routes = [r.ROUTE for r in resources]
    print('running test app on %s. routes: %s'  % (port, ' '.join(routes)))
    httpd.serve_forever()
