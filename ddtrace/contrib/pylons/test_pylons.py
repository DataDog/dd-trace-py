
import time

from nose.tools import eq_

from ... import Tracer
from ...contrib.pylons import PylonsTraceMiddleware
from ...test_tracer import DummyWriter
from ...ext import http


class FakeWSGIApp(object):

    code = None
    body = None
    headers = []
    environ = {}

    out_code = None
    out_headers = None

    def __call__(self, environ, start_response):
        start_response(self.code, self.headers)
        return self.body

    def start_response(self, status, headers):
        self.out_code = status
        self.out_headers = headers


def test_pylons():
    writer = DummyWriter()
    tracer = Tracer(writer=writer)
    app = FakeWSGIApp()
    traced = PylonsTraceMiddleware(app, tracer, service="p")

    # successful request
    assert not writer.pop()
    app.code = '200 OK'
    app.body = ['woo']
    app.environ = {
        'REQUEST_METHOD':'GET',
        'pylons.routes_dict' : {
            'controller' : 'foo',
            'action' : 'bar',
        }
    }

    start = time.time()
    out = traced(app.environ,  app.start_response)
    end = time.time()
    eq_(out, app.body)
    eq_(app.code, app.out_code)

    assert not tracer.current_span(), tracer.current_span().pprint()
    spans = writer.pop()
    assert spans, spans
    eq_(len(spans), 1)
    s = spans[0]

    eq_(s.service, "p")
    eq_(s.resource, "foo.bar")
    assert s.start >= start
    assert s.duration <= end - start
    eq_(s.error, 0)
    eq_(s.meta.get(http.STATUS_CODE), '200')

