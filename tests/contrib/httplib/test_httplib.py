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

from tests.opentracer.utils import init_tracer
from ...test_tracer import get_dummy_tracer
from ...util import assert_dict_issuperset, override_global_tracer


if PY2:
    from urllib2 import urlopen, build_opener, Request
else:
    from urllib.request import urlopen, build_opener, Request


# socket name comes from https://english.stackexchange.com/a/44048
SOCKET = 'httpbin.org'
URL_200 = 'http://{}/status/200'.format(SOCKET)
URL_500 = 'http://{}/status/500'.format(SOCKET)
URL_404 = 'http://{}/status/404'.format(SOCKET)


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

    def test_double_patch(self):
        """
        When patching httplib twice
            we shouldn't instrument twice
        """
        # setUp() calls patch, patch again
        patch()
        conn = self.get_http_connection(SOCKET)
        with contextlib.closing(conn):
            conn.request('GET', '/status/200')
            conn.getresponse()

        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 1)

    def test_double_unpatch(self):
        """
        When unpatching httplib twice
            we shouldn't break
        """
        unpatch()
        unpatch()
        conn = self.get_http_connection(SOCKET)
        with contextlib.closing(conn):
            conn.request('GET', '/status/200')
            conn.getresponse()

        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 0)

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
        request = self.get_http_connection(SOCKET)
        pin = Pin.get_from(request)
        self.assertFalse(should_skip_request(pin, request))

        # Disabled Pin and non-internal request
        self.tracer.enabled = False
        request = self.get_http_connection(SOCKET)
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
        conn = self.get_http_connection(SOCKET)
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
        assert_dict_issuperset(
            span.meta,
            {
                'http.method': 'GET',
                'http.status_code': '200',
                'http.url': URL_200,
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
        assert_dict_issuperset(
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
        conn = self.get_http_connection(SOCKET)
        with contextlib.closing(conn):
            conn.request('POST', '/status/200', body='key=value')
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
        assert_dict_issuperset(
            span.meta,
            {
                'http.method': 'POST',
                'http.status_code': '200',
                'http.url': URL_200,
            }
        )

    def test_httplib_request_get_request_query_string(self):
        """
        When making a GET request with a query string via httplib.HTTPConnection.request
            we capture a the entire url in the span
        """
        conn = self.get_http_connection(SOCKET)
        with contextlib.closing(conn):
            conn.request('GET', '/status/200?key=value&key2=value2')
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
        assert_dict_issuperset(
            span.meta,
            {
                'http.method': 'GET',
                'http.status_code': '200',
                'http.url': '{}?key=value&key2=value2'.format(URL_200),
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
            conn = self.get_http_connection(SOCKET)
            with contextlib.closing(conn):
                conn.request('GET', '/status/500')
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
        self.assertEqual(span.get_tag('http.url'), URL_500)

    def test_httplib_request_non_200_request(self):
        """
        When making a GET request via httplib.HTTPConnection.request
            when the response is a non-200
                we raise the original exception
                we mark the span as an error
                we capture the correct span tags
        """
        try:
            conn = self.get_http_connection(SOCKET)
            with contextlib.closing(conn):
                conn.request('GET', '/status/404')
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
        self.assertEqual(span.get_tag('http.url'), URL_404)

    def test_httplib_request_get_request_disabled(self):
        """
        When making a GET request via httplib.HTTPConnection.request
            when the tracer is disabled
                we do not capture any spans
        """
        self.tracer.enabled = False
        conn = self.get_http_connection(SOCKET)
        with contextlib.closing(conn):
            conn.request('GET', '/status/200')
            resp = conn.getresponse()
            self.assertEqual(self.to_str(resp.read()), '')
            self.assertEqual(resp.status, 200)

        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 0)

    def test_httplib_request_get_request_disabled_and_enabled(self):
        """
        When making a GET request via httplib.HTTPConnection.request
            when the tracer is disabled
                we do not capture any spans
        """
        self.tracer.enabled = False
        conn = self.get_http_connection(SOCKET)
        with contextlib.closing(conn):
            conn.request('GET', '/status/200')
            self.tracer.enabled = True
            resp = conn.getresponse()
            self.assertEqual(self.to_str(resp.read()), '')
            self.assertEqual(resp.status, 200)

        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 0)

    def test_urllib_request(self):
        """
        When making a request via urllib.request.urlopen
           we return the original response
           we capture a span for the request
        """
        with override_global_tracer(self.tracer):
            resp = urlopen(URL_200)

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
        self.assertEqual(span.get_tag('http.url'), URL_200)

    def test_urllib_request_https(self):
        """
        When making a request via urllib.request.urlopen
           when making an HTTPS connection
               we return the original response
               we capture a span for the request
        """
        with override_global_tracer(self.tracer):
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
        req = Request(URL_200)
        with override_global_tracer(self.tracer):
            resp = urlopen(req)

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
        self.assertEqual(span.get_tag('http.url'), URL_200)

    def test_urllib_request_opener(self):
        """
        When making a request via urllib.request.OpenerDirector
           we return the original response
           we capture a span for the request
        """
        opener = build_opener()
        with override_global_tracer(self.tracer):
            resp = opener.open(URL_200)

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
        self.assertEqual(span.get_tag('http.url'), URL_200)

    def test_httplib_request_get_request_ot(self):
        """ OpenTracing version of test with same name. """
        ot_tracer = init_tracer('my_svc', self.tracer)

        with ot_tracer.start_active_span('ot_span'):
            conn = self.get_http_connection(SOCKET)
            with contextlib.closing(conn):
                conn.request('GET', '/status/200')
                resp = conn.getresponse()
                self.assertEqual(self.to_str(resp.read()), '')
                self.assertEqual(resp.status, 200)

        spans = self.tracer.writer.pop()
        self.assertEqual(len(spans), 2)
        ot_span, dd_span = spans

        # confirm the parenting
        self.assertEqual(ot_span.parent_id, None)
        self.assertEqual(dd_span.parent_id, ot_span.span_id)

        self.assertEqual(ot_span.service, 'my_svc')
        self.assertEqual(ot_span.name, 'ot_span')

        self.assertEqual(dd_span.span_type, 'http')
        self.assertEqual(dd_span.name, self.SPAN_NAME)
        self.assertEqual(dd_span.error, 0)
        assert_dict_issuperset(
            dd_span.meta,
            {
                'http.method': 'GET',
                'http.status_code': '200',
                'http.url': URL_200,
            }
        )

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
            with override_global_tracer(self.tracer):
                resp = urllib.urlopen(URL_200)

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
            self.assertEqual(span.get_tag('http.url'), URL_200)

        def test_urllib_request_https(self):
            """
            When making a request via urllib.urlopen
               when making an HTTPS connection
                   we return the original response
                   we capture a span for the request
            """
            with override_global_tracer(self.tracer):
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
