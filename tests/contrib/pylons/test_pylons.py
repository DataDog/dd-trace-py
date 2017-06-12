import time

from nose.tools import eq_

from ddtrace import Tracer
from ddtrace.contrib.pylons import PylonsTraceMiddleware
from ddtrace.ext import http

from ...test_tracer import DummyWriter


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

    def start_response_exception(self, status, headers):
        e = Exception("Some exception")
        e.code = 'wrong formatted code'
        raise e


def test_pylons():
    writer = DummyWriter()
    tracer = Tracer()
    tracer.writer = writer
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

def test_pylons_exceptions():
    writer = DummyWriter()
    tracer = Tracer()
    tracer.writer = writer
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

    try:
        out = traced(app.environ,  app.start_response_exception)
    except Exception as e:
        pass

    assert not tracer.current_span(), tracer.current_span().pprint()
    spans = writer.pop()
    assert spans, spans
    eq_(len(spans), 1)
    s = spans[0]

    eq_(s.error, 1)
    eq_(s.get_tag("error.msg"), "Some exception")
    sc = int(s.get_tag("http.status_code"))
    assert sc >= 100 and sc < 600
    assert s.get_tag("error.stack")
