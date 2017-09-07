import time

from nose.tools import eq_, ok_

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

    def start_response_string_code(self, status, headers):
        e = Exception("Custom exception")
        e.code = '512'
        raise e


def test_pylons():
    writer = DummyWriter()
    tracer = Tracer()
    tracer.writer = writer
    app = FakeWSGIApp()
    traced = PylonsTraceMiddleware(app, tracer, service="p")

    # successful request
    eq_(writer.pop(), [])
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

    eq_(tracer.current_span(), None)
    spans = writer.pop()
    ok_(spans, spans)
    eq_(len(spans), 1)
    s = spans[0]

    eq_(s.service, "p")
    eq_(s.resource, "foo.bar")
    ok_(s.start >= start)
    ok_(s.duration <= end - start)
    eq_(s.error, 0)
    eq_(s.meta.get(http.STATUS_CODE), '200')

def test_pylons_exceptions():
    # ensures the reported status code is 500 even if a wrong
    # status code is set and that the stacktrace points to the
    # right function
    writer = DummyWriter()
    tracer = Tracer()
    tracer.writer = writer
    app = FakeWSGIApp()
    traced = PylonsTraceMiddleware(app, tracer, service="p")

    # successful request
    eq_(writer.pop(), [])
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

    eq_(tracer.current_span(), None)
    spans = writer.pop()
    ok_(spans, spans)
    eq_(len(spans), 1)
    s = spans[0]

    eq_(s.error, 1)
    eq_(s.get_tag('error.msg'), 'Some exception')
    eq_(int(s.get_tag('http.status_code')), 500)
    ok_('start_response_exception' in s.get_tag('error.stack'))
    ok_('Exception: Some exception' in s.get_tag('error.stack'))

def test_pylons_string_code():
    writer = DummyWriter()
    tracer = Tracer()
    tracer.writer = writer
    app = FakeWSGIApp()
    traced = PylonsTraceMiddleware(app, tracer, service="p")

    # successful request
    eq_(writer.pop(), [])
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
        out = traced(app.environ,  app.start_response_string_code)
    except Exception as e:
        pass

    eq_(tracer.current_span(), None)
    spans = writer.pop()
    ok_(spans, spans)
    eq_(len(spans), 1)
    s = spans[0]

    eq_(s.error, 1)
    eq_(s.get_tag("error.msg"), "Custom exception")
    sc = int(s.get_tag("http.status_code"))
    eq_(sc, 512)
    ok_(s.get_tag("error.stack"))
