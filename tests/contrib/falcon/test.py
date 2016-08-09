
# 3p
import falcon
import falcon.testing
from nose.tools import eq_
from nose.plugins.attrib import attr

# project
from ddtrace import Tracer
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
        self._tracer = Tracer()
        self._writer = DummyWriter()
        self._tracer.writer = self._writer
        self._service = "my-falcon"

        self.api = falcon.API(middleware=[TraceMiddleware(self._tracer, self._service)])

        resources = [
            Resource200,
            Resource500,
            ResourceExc,
        ]
        for r in resources:
            self.api.add_route(r.ROUTE, r())

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
        eq_(span.resource, "GET tests.contrib.falcon.test.ResourceExc")
        eq_(span.get_tag(httpx.STATUS_CODE), '500')
        eq_(span.name, "falcon.request")

    def test_200(self):
        out = self.simulate_get(Resource200.ROUTE)
        eq_(out.status_code, 200)
        eq_(out.content, Resource200.BODY)

        spans = self._writer.pop()
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self._service)
        eq_(span.resource, "GET tests.contrib.falcon.test.Resource200")
        eq_(span.get_tag(httpx.STATUS_CODE), '200')
        eq_(span.name, "falcon.request")

    def test_500(self):
        out = self.simulate_get(Resource500.ROUTE)
        eq_(out.status_code, 500)
        eq_(out.content, Resource500.BODY)

        spans = self._writer.pop()
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.service, self._service)
        eq_(span.resource, "GET tests.contrib.falcon.test.Resource500")
        eq_(span.get_tag(httpx.STATUS_CODE), '500')
        eq_(span.name, "falcon.request")


