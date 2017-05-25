import gc

from unittest import TestCase
from nose.tools import eq_

from ddtrace.contrib.flask import TraceMiddleware
from ...test_tracer import get_dummy_tracer

from flask import Flask


class FlaskBlinkerCase(TestCase):
    """Ensures that the integration between Flask and Blinker
    to trace Flask endpoints works as expected
    """
    def get_app(self):
        """Creates a new Flask App"""
        app = Flask(__name__)

        # add testing routes here
        @app.route('/')
        def index():
            return 'Hello world!'

        return app

    def setUp(self):
        # initialize a traced app with a dummy tracer
        app = self.get_app()
        self.tracer = get_dummy_tracer()
        self.traced_app = TraceMiddleware(app, self.tracer)

        # make the app testable
        app.config['TESTING'] = True
        self.app = app.test_client()

    def test_signals_without_weak_references(self):
        # it should work when the traced_app reference is not
        # stored by the user and the garbage collection starts
        self.traced_app = None
        gc.collect()

        r = self.app.get('/')
        eq_(r.status_code, 200)

        traces = self.tracer.writer.pop_traces()
        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)

        span = traces[0][0]
        eq_(span.service, 'flask')
        eq_(span.name, 'flask.request')
        eq_(span.span_type, 'http')
        eq_(span.resource, 'index')
        eq_(span.get_tag('http.status_code'), '200')
        eq_(span.get_tag('http.url'), 'http://localhost/')
        eq_(span.error, 0)
