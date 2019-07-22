import json

import webapp2

from ddtrace.constants import SAMPLING_PRIORITY_KEY
from ddtrace.contrib.webapp2 import Webapp2TraceMiddleware
from ddtrace.ext import errors, http
from ...base import BaseTracerTestCase


class MyHandler(webapp2.RequestHandler):
    def post(self):
        json_body = self.request.json
        payload = json.dumps(json_body)
        self.response.write(payload)


class Webapp2TestCase(BaseTracerTestCase):
    def setUp(self):
        super(Webapp2TestCase, self).setUp()
        handler = MyHandler
        route = webapp2.Route('/tests/', handler, methods=['POST'])
        route1 = webapp2.Route('/unused/', handler, methods=['POST'])
        routes = [route, route1]
        self.app = webapp2.WSGIApplication(routes=routes)
        self.traced_app = Webapp2TraceMiddleware(
            self.app, self.tracer, service='test-service')

    def test_apm_middleware_valid_post(self):
        # Given an application instrumented with a tracer which captures spans
        # When doing a basic POST request
        data = {'valid': 'test'}
        payload = json.dumps(data)
        request = webapp2.Request.blank('/tests/?q=example', POST=payload)
        response = request.get_response(self.traced_app)

        # Then the response will be successful
        assert response.status_code == 200
        assert response.text == payload
        # And the tracer will contain a span with the correct tags for
        # a POST request
        span, = self.tracer.writer.pop()
        assert span.span_type == 'http'
        assert span.service == 'test-service'
        assert span.resource == 'tests.contrib.webapp2.test_webapp2.MyHandler'
        assert span.error == 0
        assert span.get_tag(http.URL) == 'http://localhost/tests/?q=example'
        assert span.get_tag(http.METHOD) == 'POST'
        assert span.get_tag(http.STATUS_CODE) == '200'

    def test_apm_middleware_404(self):
        # Given an application instrumented with our middleware
        # When doing a request for a non-existent resource
        request = webapp2.Request.blank('/non-existent')
        request.get_response(self.traced_app)

        # Then the tracer will contain a span with the correct tags for
        # a 404 request
        span, = self.tracer.writer.pop()
        assert span.span_type == 'http'
        assert span.service == 'test-service'
        assert span.resource == 'http://localhost/non-existent'
        assert span.error == 0
        assert span.get_tag(http.URL) == 'http://localhost/non-existent'
        assert span.get_tag(http.METHOD) == 'GET'
        assert span.get_tag(http.STATUS_CODE) == '404'
        assert span.get_tag(errors.ERROR_MSG) == '404 Not Found\n\nThe resource could not be found.\n\n   '

    def test_apm_middleware_post_error(self):
        # Given an application instrumented with a tracer which captures spans
        # When doing an invalid POST request
        payload = 'invalid json'
        request = webapp2.Request.blank('/tests/', POST=payload)
        request.get_response(self.traced_app)

        # Then the tracer will contain a span with the correct tags for
        # a POST request with an error
        span, = self.tracer.writer.pop()
        assert span.span_type == 'http'
        assert span.service == 'test-service'
        assert span.resource == 'tests.contrib.webapp2.test_webapp2.MyHandler'
        assert span.error == 1
        assert span.get_tag(http.URL) == 'http://localhost/tests/'
        assert span.get_tag(http.METHOD) == 'POST'
        assert span.get_tag(http.STATUS_CODE) == '500'
        assert span.get_tag(errors.ERROR_MSG) == (
            '500 Internal Server Error\n\nThe server has either erred or '
            'is incapable of performing the requested operation.\n\n   ')

    def test_distributed_tracing_default(self):
        # Given that distributed tracing is enabled by default
        # When doing a request which contains distributed tracing headers
        headers = {
            'x-datadog-trace-id': '100',
            'x-datadog-parent-id': '42',
            'x-datadog-sampling-priority': '2',
        }
        data = {'valid': 'test'}
        payload = json.dumps(data)
        request = webapp2.Request.blank('/tests/', POST=payload)
        request.headers = headers
        request.get_response(self.traced_app)

        # Then the span will contain the data which was passed in the headers
        span, = self.tracer.writer.pop()
        assert span.trace_id == 100
        assert span.parent_id == 42
        assert span.get_metric(SAMPLING_PRIORITY_KEY) == 2

    def test_distributed_tracing_disabled(self):
        # Given that distributed tracing is explicitly disabled
        app = Webapp2TraceMiddleware(
            self.app, self.tracer, service='test-service',
            distributed_tracing=False)

        # When doing a request which contains distributed tracing headers
        headers = {
            'x-datadog-trace-id': '100',
            'x-datadog-parent-id': '42',
            'x-datadog-sampling-priority': '2',
        }
        data = {'valid': 'test'}
        payload = json.dumps(data)
        request = webapp2.Request.blank('/tests/', POST=payload)
        request.headers = headers
        request.get_response(app)

        # Then the span will contain the data which was passed in the headers
        span, = self.tracer.writer.pop()
        assert span.trace_id != 100
        assert span.parent_id is None
        assert span.get_metric(SAMPLING_PRIORITY_KEY) != 2
