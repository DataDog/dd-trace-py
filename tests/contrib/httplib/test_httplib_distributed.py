# Standard library
import contextlib
import re

import wrapt

# Project
from ddtrace import config
from ddtrace.internal.compat import httplib
from ddtrace.pin import Pin
from tests.utils import TracerTestCase
from tests.utils import get_128_bit_trace_id_from_headers

from .test_httplib import HTTPLibBaseMixin
from .test_httplib import SOCKET


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
        print("come here")
        print(r"{}".format(httplib_request_str))
        # we need to pull the trace_id values out of the response.message str
        x_datadog_trace_id = re.search(r"x-datadog-trace-id: (.*?)\\", httplib_request_str).group(1)
        _dd_p_tid = re.search(r"_dd.p.tid=(.*?)(;|\\|$)", httplib_request_str).group(1)
        # _dd.p.tid=652eb5cd00000000
        header_t_id = get_128_bit_trace_id_from_headers(
            {"x-datadog-trace-id": x_datadog_trace_id, "x-datadog-tags": f"_dd.p.tid={_dd_p_tid}"}
        )
        header_t_id = get_128_bit_trace_id_from_headers(self.httplib_request.decode())
        assert str(root_span.trace_id).encode("utf-8") == header_t_id
        return True

    def headers_not_here(self, tracer):
        assert b"x-datadog-trace-id" not in self.httplib_request
        assert b"x-datadog-parent-id" not in self.httplib_request
        return True

    def get_http_connection(self, *args, **kwargs):
        conn = httplib.HTTPConnection(*args, **kwargs)
        Pin.override(conn, tracer=self.tracer)
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
        cfg = config.get_from(conn)
        cfg["distributed_tracing"] = True
        self.request(conn=conn)
        self.check_enabled()

    def test_propagation_connection_false(self):
        conn = self.get_http_connection(SOCKET)
        cfg = config.get_from(conn)
        cfg["distributed_tracing"] = False
        self.request(conn=conn)
        self.check_disabled()
