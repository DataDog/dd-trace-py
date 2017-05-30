# Standard library
import contextlib
import sys
import unittest

# Third party
import wrapt

# Project
from ddtrace.compat import httplib, PY2
from ddtrace.contrib.httplib import patch, unpatch
from ddtrace.contrib.httplib.patch import should_skip_request
from ddtrace.pin import Pin
from ...test_tracer import get_dummy_tracer

if PY2:
    from urllib2 import urlopen, build_opener, Request
else:
    from urllib.request import urlopen, build_opener, Request


# Base test mixin for shared tests between Py2 and Py3
class HTTPLibBaseMixin(object):
    SPAN_NAME = 'httplib.request' if PY2 else 'http.client.request'

    def to_str(self, value):
        return value.decode('utf-8')

    def setUp(self):
        patch()
        self.tracer = get_dummy_tracer()
        Pin.override(httplib, tracer=self.tracer)

    def tearDown(self):
        unpatch()


# Main test cases for httplib/http.client and urllib2/urllib.request
class HTTPLibTestCase(HTTPLibBaseMixin, unittest.TestCase):
    SPAN_NAME = 'httplib.request' if PY2 else 'http.client.request'

    def to_str(self, value):
        """Helper method to decode a string or byte object to a string"""
        return value.decode('utf-8')

    def get_http_connection(self, *args, **kwargs):
        conn = httplib.HTTPConnection(*args, **kwargs)
        Pin.override(conn, tracer=self.tracer)
        return conn

    def get_https_connection(self, *args, **kwargs):
        conn = httplib.HTTPSConnection(*args, **kwargs)
        Pin.override(conn, tracer=self.tracer)
        return conn

    def setUp(self):
        patch()
        self.tracer = get_dummy_tracer()

    def tearDown(self):
        unpatch()

    def test_patch(self):
        """
        When patching httplib
            we patch the correct module/methods
        """
        self.assertIsInstance(httplib.HTTPConnection.__init__, wrapt.BoundFunctionWrapper)
        self.assertIsInstance(httplib.HTTPConnection.putrequest, wrapt.BoundFunctionWrapper)
        self.assertIsInstance(httplib.HTTPConnection.getresponse, wrapt.BoundFunctionWrapper)

    def test_unpatch(self):
        """
        When unpatching httplib
            we restore the correct module/methods
        """
        original_init = httplib.HTTPConnection.__init__.__wrapped__
        original_putrequest = httplib.HTTPConnection.putrequest.__wrapped__
        original_getresponse = httplib.HTTPConnection.getresponse.__wrapped__
        unpatch()

        self.assertEqual(httplib.HTTPConnection.__init__, original_init)
        self.assertEqual(httplib.HTTPConnection.putrequest, original_putrequest)
        self.assertEqual(httplib.HTTPConnection.getresponse, original_getresponse)

    def test_should_skip_request(self):
        """
        When calling should_skip_request
            with an enabled Pin and non-internal request
                returns False
            with a disabled Pin and non-internal request
                returns True
            with an enabled Pin and internal request
                returns True
            with a disabled Pin and internal request
                returns True
        """
        # Enabled Pin and non-internal request
        self.tracer.enabled = True
        request = self.get_http_connection('httpstat.us')
        pin = Pin.get_from(request)
        self.assertFalse(should_skip_request(pin, request))

        # Disabled Pin and non-internal request
        self.tracer.enabled = False
        request = self.get_http_connection('httpstat.us')
        pin = Pin.get_from(request)
        self.assertTrue(should_skip_request(pin, request))

        # Enabled Pin and internal request
        self.tracer.enabled = True
        request = self.get_http_connection(self.tracer.writer.api.hostname, self.tracer.writer.api.port)
        pin = Pin.get_from(request)
        self.assertTrue(should_skip_request(pin, request))

        # Disabled Pin and internal request
        self.tracer.enabled = False
        request = self.get_http_connection(self.tracer.writer.api.hostname, self.tracer.writer.api.port)
        pin = Pin.get_from(request)
        self.assertTrue(should_skip_request(pin, request))

    def test_httplib_request_get_request(self):
        """
        When making a GET request via httplib.HTTPConnection.request
            we return the original response
            we capture a span for the request
        """
        conn = self.get_http_connection('httpstat.us')
        with contextlib.closing(conn):
            conn.request('GET', '/200')
            resp = conn.getresponse()
            self.assertEqual(self.to_str(resp.read()), '200 OK')
            self.assertEqual(resp.status, 200)

        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.span_type, 'http')
        self.assertIsNone(span.service)
        self.assertEqual(span.name, self.SPAN_NAME)
        self.assertEqual(span.error, 0)
        self.assertDictEqual(
            span.meta,
            {
                'http.method': 'GET',
                'http.status_code': '200',
                'http.url': 'http://httpstat.us/200',
            }
        )

    def test_httplib_request_get_request_https(self):
        """
        When making a GET request via httplib.HTTPConnection.request
            when making an HTTPS connection
                we return the original response
                we capture a span for the request
        """
        conn = self.get_https_connection('httpbin.org')
        with contextlib.closing(conn):
            conn.request('GET', '/status/200')
            resp = conn.getresponse()
            self.assertEqual(self.to_str(resp.read()), '')
            self.assertEqual(resp.status, 200)

        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.span_type, 'http')
        self.assertIsNone(span.service)
        self.assertEqual(span.name, self.SPAN_NAME)
        self.assertEqual(span.error, 0)
        self.assertDictEqual(
            span.meta,
            {
                'http.method': 'GET',
                'http.status_code': '200',
                'http.url': 'https://httpbin.org/status/200',
            }
        )

    def test_httplib_request_post_request(self):
        """
        When making a POST request via httplib.HTTPConnection.request
            we return the original response
            we capture a span for the request
        """
        conn = self.get_http_connection('httpstat.us')
        with contextlib.closing(conn):
            conn.request('POST', '/200', body='key=value')
            resp = conn.getresponse()
            self.assertEqual(self.to_str(resp.read()), '200 OK')
            self.assertEqual(resp.status, 200)

        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.span_type, 'http')
        self.assertIsNone(span.service)
        self.assertEqual(span.name, self.SPAN_NAME)
        self.assertEqual(span.error, 0)
        self.assertDictEqual(
            span.meta,
            {
                'http.method': 'POST',
                'http.status_code': '200',
                'http.url': 'http://httpstat.us/200',
            }
        )

    def test_httplib_request_get_request_query_string(self):
        """
        When making a GET request with a query string via httplib.HTTPConnection.request
            we capture a the entire url in the span
        """
        conn = self.get_http_connection('httpstat.us')
        with contextlib.closing(conn):
            conn.request('GET', '/200?key=value&key2=value2')
            resp = conn.getresponse()
            self.assertEqual(self.to_str(resp.read()), '200 OK')
            self.assertEqual(resp.status, 200)

        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.span_type, 'http')
        self.assertIsNone(span.service)
        self.assertEqual(span.name, self.SPAN_NAME)
        self.assertEqual(span.error, 0)
        self.assertDictEqual(
            span.meta,
            {
                'http.method': 'GET',
                'http.status_code': '200',
                'http.url': 'http://httpstat.us/200?key=value&key2=value2',
            }
        )

    def test_httplib_request_500_request(self):
        """
        When making a GET request via httplib.HTTPConnection.request
            when the response is a 500
                we raise the original exception
                we mark the span as an error
                we capture the correct span tags
        """
        try:
            conn = self.get_http_connection('httpstat.us')
            with contextlib.closing(conn):
                conn.request('GET', '/500')
                conn.getresponse()
        except httplib.HTTPException:
            resp = sys.exc_info()[1]
            self.assertEqual(self.to_str(resp.read()), '500 Internal Server Error')
            self.assertEqual(resp.status, 500)

        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.span_type, 'http')
        self.assertIsNone(span.service)
        self.assertEqual(span.name, self.SPAN_NAME)
        self.assertEqual(span.error, 1)
        self.assertEqual(span.get_tag('http.method'), 'GET')
        self.assertEqual(span.get_tag('http.status_code'), '500')
        self.assertEqual(span.get_tag('http.url'), 'http://httpstat.us/500')

    def test_httplib_request_non_200_request(self):
        """
        When making a GET request via httplib.HTTPConnection.request
            when the response is a non-200
                we raise the original exception
                we mark the span as an error
                we capture the correct span tags
        """
        try:
            conn = self.get_http_connection('httpstat.us')
            with contextlib.closing(conn):
                conn.request('GET', '/404')
                conn.getresponse()
        except httplib.HTTPException:
            resp = sys.exc_info()[1]
            self.assertEqual(self.to_str(resp.read()), '404 Not Found')
            self.assertEqual(resp.status, 404)

        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.span_type, 'http')
        self.assertIsNone(span.service)
        self.assertEqual(span.name, self.SPAN_NAME)
        self.assertEqual(span.error, 0)
        self.assertEqual(span.get_tag('http.method'), 'GET')
        self.assertEqual(span.get_tag('http.status_code'), '404')
        self.assertEqual(span.get_tag('http.url'), 'http://httpstat.us/404')

    def test_httplib_request_get_request_disabled(self):
        """
        When making a GET request via httplib.HTTPConnection.request
            when the tracer is disabled
                we do not capture any spans
        """
        self.tracer.enabled = False
        conn = self.get_http_connection('httpstat.us')
        with contextlib.closing(conn):
            conn.request('GET', '/200')
            resp = conn.getresponse()
            self.assertEqual(self.to_str(resp.read()), '200 OK')
            self.assertEqual(resp.status, 200)

        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 0)

    def test_urllib_request(self):
        """
        When making a request via urllib.request.urlopen
           we return the original response
           we capture a span for the request
        """
        resp = urlopen('http://httpstat.us/200')
        self.assertEqual(self.to_str(resp.read()), '200 OK')
        self.assertEqual(resp.getcode(), 200)

        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.span_type, 'http')
        self.assertIsNone(span.service)
        self.assertEqual(span.name, self.SPAN_NAME)
        self.assertEqual(span.error, 0)
        self.assertEqual(span.get_tag('http.method'), 'GET')
        self.assertEqual(span.get_tag('http.status_code'), '200')
        self.assertEqual(span.get_tag('http.url'), 'http://httpstat.us/200')

    def test_urllib_request_https(self):
        """
        When making a request via urllib.request.urlopen
           when making an HTTPS connection
               we return the original response
               we capture a span for the request
        """
        resp = urlopen('https://httpbin.org/status/200')
        self.assertEqual(self.to_str(resp.read()), '')
        self.assertEqual(resp.getcode(), 200)

        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.span_type, 'http')
        self.assertIsNone(span.service)
        self.assertEqual(span.name, self.SPAN_NAME)
        self.assertEqual(span.error, 0)
        self.assertEqual(span.get_tag('http.method'), 'GET')
        self.assertEqual(span.get_tag('http.status_code'), '200')
        self.assertEqual(span.get_tag('http.url'), 'https://httpbin.org/status/200')

    def test_urllib_request_object(self):
        """
        When making a request via urllib.request.urlopen
           with a urllib.request.Request object
               we return the original response
               we capture a span for the request
        """
        req = Request('http://httpstat.us/200')
        resp = urlopen(req)
        self.assertEqual(self.to_str(resp.read()), '200 OK')
        self.assertEqual(resp.getcode(), 200)

        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.span_type, 'http')
        self.assertIsNone(span.service)
        self.assertEqual(span.name, self.SPAN_NAME)
        self.assertEqual(span.error, 0)
        self.assertEqual(span.get_tag('http.method'), 'GET')
        self.assertEqual(span.get_tag('http.status_code'), '200')
        self.assertEqual(span.get_tag('http.url'), 'http://httpstat.us/200')

    def test_urllib_request_opener(self):
        """
        When making a request via urllib.request.OpenerDirector
           we return the original response
           we capture a span for the request
        """
        opener = build_opener()
        resp = opener.open('http://httpstat.us/200')
        self.assertEqual(self.to_str(resp.read()), '200 OK')
        self.assertEqual(resp.getcode(), 200)

        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertEqual(span.span_type, 'http')
        self.assertIsNone(span.service)
        self.assertEqual(span.name, self.SPAN_NAME)
        self.assertEqual(span.error, 0)
        self.assertEqual(span.get_tag('http.method'), 'GET')
        self.assertEqual(span.get_tag('http.status_code'), '200')
        self.assertEqual(span.get_tag('http.url'), 'http://httpstat.us/200')


# Additional Python2 test cases for urllib
if PY2:
    import urllib

    class HTTPLibPython2Test(HTTPLibBaseMixin, unittest.TestCase):
        def test_urllib_request(self):
            """
            When making a request via urllib.urlopen
               we return the original response
               we capture a span for the request
            """
            resp = urllib.urlopen('http://httpstat.us/200')
            self.assertEqual(resp.read(), '200 OK')
            self.assertEqual(resp.getcode(), 200)

            spans = self.tracer.writer.pop()
            self.assertEqual(len(spans), 1)
            span = spans[0]
            self.assertEqual(span.span_type, 'http')
            self.assertIsNone(span.service)
            self.assertEqual(span.name, 'httplib.request')
            self.assertEqual(span.error, 0)
            self.assertEqual(span.get_tag('http.method'), 'GET')
            self.assertEqual(span.get_tag('http.status_code'), '200')
            self.assertEqual(span.get_tag('http.url'), 'http://httpstat.us/200')

        def test_urllib_request_https(self):
            """
            When making a request via urllib.urlopen
               when making an HTTPS connection
                   we return the original response
                   we capture a span for the request
            """
            resp = urllib.urlopen('https://httpbin.org/status/200')
            self.assertEqual(resp.read(), '')
            self.assertEqual(resp.getcode(), 200)

            spans = self.tracer.writer.pop()
            self.assertEqual(len(spans), 1)
            span = spans[0]
            self.assertEqual(span.span_type, 'http')
            self.assertIsNone(span.service)
            self.assertEqual(span.name, 'httplib.request')
            self.assertEqual(span.error, 0)
            self.assertEqual(span.get_tag('http.method'), 'GET')
            self.assertEqual(span.get_tag('http.status_code'), '200')
            self.assertEqual(span.get_tag('http.url'), 'https://httpbin.org/status/200')
