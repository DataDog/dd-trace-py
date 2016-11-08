

import logging
import sys

# 3p
import bottle
from nose.tools import eq_, ok_
import webtest

# project
from ddtrace import tracer, compat
from ddtrace.contrib.bottle import TracePlugin
from tests.test_tracer import get_test_tracer


SERVICE = "foobar"

def test_200():
    # setup our test app
    app = bottle.Bottle()
    @app.route('/hi/<name>')
    def hi(name):
        return 'hi %s' % name
    tracer, app = _trace_app(app)

    # make a request
    resp = app.get("/hi/dougie")
    eq_(resp.status_int, 200)
    eq_(compat.to_unicode(resp.body), u'hi dougie')
    # validate it's traced
    spans = tracer.writer.pop()
    eq_(len(spans), 1)
    s = spans[0]
    eq_(s.name, "bottle.request")
    eq_(s.service, "foobar")
    eq_(s.resource, "GET /hi/<name>")
    eq_(s.get_tag('http.status_code'), '200')
    eq_(s.get_tag('http.method'), 'GET')

def test_500():
    # setup our test app
    app = bottle.Bottle()

    @app.route('/hi')
    def hi():
        raise Exception("oh no")

    tracer, app = _trace_app(app)

    # make a request
    try:
        resp = app.get("/hi")
        eq_(resp.status_int, 500)
    except Exception:
        pass

    spans = tracer.writer.pop()
    eq_(len(spans), 1)
    s = spans[0]
    eq_(s.name, "bottle.request")
    eq_(s.service, "foobar")
    eq_(s.resource, "GET /hi")
    eq_(s.get_tag('http.status_code'), '500')
    eq_(s.get_tag('http.method'), 'GET')


def _trace_app(app):
    tracer = get_test_tracer()
    app.install(TracePlugin(service=SERVICE, tracer=tracer))
    return tracer, webtest.TestApp(app)
