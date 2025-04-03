# Standard library
import contextlib
import http.client as httplib

import wrapt

# Project
from ddtrace import config
from ddtrace._trace.span import _get_64_highest_order_bits_as_hex
from ddtrace.trace import Pin
from tests.utils import TracerTestCase

from .test_httplib import SOCKET
from .test_httplib import HTTPLibBaseMixin


class TestHTTPLibDistributed(HTTPLibBaseMixin, TracerTestCase):
    def setUp(self):
        super(TestHTTPLibDistributed, self).setUp()
        self.httplib_request = b""

    def send(self, func, instance, args, kwargs):
        self.httplib_request += args[0]
        return func(*args, **kwargs)

    def headers_here(self, tracer, root_span):
        assert b"x-datadog-trace-id" in self.httplib_request
        assert b"x-datadog-parent-id" in self.httplib_request
        httplib_request_str = self.httplib_request.decode()
        assert str(root_span._trace_id_64bits) in httplib_request_str
        assert _get_64_highest_order_bits_as_hex(root_span.trace_id) in httplib_request_str
        return True

    def headers_not_here(self, tracer):
        assert b"x-datadog-trace-id" not in self.httplib_request
        assert b"x-datadog-parent-id" not in self.httplib_request
        return True

    def get_http_connection(self, *args, **kwargs):
        conn = httplib.HTTPConnection(*args, **kwargs)
        Pin._override(conn, tracer=self.tracer)
        return conn

    def request(self, conn=None):
        conn = conn or self.get_http_connection(SOCKET)
        with contextlib.closing(conn):
            conn.send = wrapt.FunctionWrapper(conn.send, self.send)
            conn.request("POST", "/status/200", body="key=value")
            conn.getresponse()

    def check_enabled(self):
        spans = self.pop_spans()
        self.assertEqual(len(spans), 1)
        span = spans[0]
        assert self.headers_here(self.tracer, span)

    def check_disabled(self):
        spans = self.pop_spans()
        self.assertEqual(len(spans), 1)
        assert self.headers_not_here(self.tracer)

    def test_propagation(self):
        with self.override_config("httplib", dict(distributed_tracing=True)):
            self.request()
        self.check_enabled()

    def test_propagation_disabled(self):
        with self.override_config("httplib", dict(distributed_tracing=False)):
            self.request()
        self.check_disabled()

    def test_propagation_connection_true(self):
        conn = self.get_http_connection(SOCKET)
        cfg = config._get_from(conn)
        cfg["distributed_tracing"] = True
        self.request(conn=conn)
        self.check_enabled()

    def test_propagation_connection_false(self):
        conn = self.get_http_connection(SOCKET)
        cfg = config._get_from(conn)
        cfg["distributed_tracing"] = False
        self.request(conn=conn)
        self.check_disabled()
