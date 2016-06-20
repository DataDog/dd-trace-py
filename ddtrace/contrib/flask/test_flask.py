
import time
import logging

from flask import Flask
from nose.tools import eq_

from tracer import Tracer
from tracer.contrib.flask import TraceMiddleware
from tracer.test_tracer import DummyWriter

log = logging.getLogger(__name__)

# global writer tracer for the tests.
writer = DummyWriter()
tracer = Tracer(writer=writer)


class TestError(Exception): pass


# define a toy flask app.
app = Flask(__name__)

@app.route('/')
def index():
    return 'hello'

@app.route('/error')
def error():
    raise TestError()

@app.route('/child')
def child():
    with tracer.trace('child') as span:
        span.set_tag('a', 'b')
        return 'child'

@app.errorhandler(TestError)
def handle_my_exception(e):
    assert isinstance(e, TestError)
    return 'error', 500


# add tracing to the app (we use a global app to help ensure multiple requests
# work)
service = "test.flask.service"
assert not writer.pop() # should always be empty
traced_app = TraceMiddleware(app, tracer, service=service)

# make the app testable
app.config['TESTING'] = True
app = app.test_client()


class TestFlask(object):

    def setUp(self):
        from nose.plugins.skip import SkipTest
        raise SkipTest("matt")

        # ensure the last test didn't leave any trash
        spans = writer.pop()
        assert not spans, spans
        assert not tracer.current_span(), tracer.current_span()

    def test_child(self):
        start = time.time()
        rv = app.get('/child')
        end = time.time()
        # ensure request worked
        eq_(rv.status_code, 200)
        eq_(rv.data, 'child')
        # ensure trace worked
        spans = writer.pop()
        eq_(len(spans), 2)

        spans_by_name = {s.name:s for s in spans}

        s = spans_by_name['flask.request']
        assert s.span_id
        assert s.trace_id
        assert not s.parent_id
        eq_(s.service, service)
        eq_(s.resource, "child")
        assert s.start >= start
        assert s.duration <= end - start
        eq_(s.error, 0)

        c = spans_by_name['child']
        assert c.span_id
        eq_(c.trace_id, s.trace_id)
        eq_(c.parent_id, s.span_id)
        eq_(c.service, service)
        eq_(c.resource, 'child')
        assert c.start >= start
        assert c.duration <= end - start
        eq_(c.error, 0)

    def test_success(self):
        start = time.time()
        rv = app.get('/')
        end = time.time()

        # ensure request worked
        eq_(rv.status_code, 200)
        eq_(rv.data, 'hello')

        # ensure trace worked
        assert not tracer.current_span(), tracer.current_span().pprint()
        spans = writer.pop()
        eq_(len(spans), 1)
        s = spans[0]
        eq_(s.service, service)
        eq_(s.resource, "index")
        assert s.start >= start
        assert s.duration <= end - start
        eq_(s.error, 0)

    def test_error(self):
        start = time.time()
        rv = app.get('/error')
        end = time.time()

        # ensure the request itself worked
        eq_(rv.status_code, 500)
        eq_(rv.data, 'error')

        # ensure the request was traced.
        assert not tracer.current_span()
        spans = writer.pop()
        eq_(len(spans), 1)
        s = spans[0]
        eq_(s.service, service)
        eq_(s.resource, "error")
        assert s.start >= start
        assert s.duration <= end - start


