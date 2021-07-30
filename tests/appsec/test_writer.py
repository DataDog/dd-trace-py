import functools
import json
import random
import sys
import threading
import time
import unittest

import attr
import mock
import six

from ddtrace.internal import compat
from ddtrace.appsec.internal.writer import HTTPEventWriter


if sys.version_info[0] >= 3:
    from http import server
else:
    import BaseHTTPServer as server


@attr.s
class FakeEvent(object):
    foo = attr.ib(default="bar")


class FakeAppSecHandler(server.BaseHTTPRequestHandler):

    protocol_version = "HTTP/1.1"

    def handle_one_request(self):
        try:
            return server.BaseHTTPRequestHandler.handle_one_request(self)
        except Exception:
            self.server.exceptions.append(sys.exc_info())
            raise

    def do_POST(self):
        self.server.num_requests += 1

        assert self.path.endswith("v1/input")
        assert self.headers.get("Content-Type") == "application/json"

        # Keep the request body
        payload = self.rfile.read(int(self.headers.get("Content-Length")))
        self.server.payloads.append(payload)

        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")


class IncorrectVersionHandler(server.BaseHTTPRequestHandler):

    protocol_version = "HTTP/1.1"

    def do_POST(self):
        self.server.num_requests += 1

        # Agent without AppSec support returns 404
        self.send_error(404)
        self.end_headers()


class DisabledAppSecHandler(server.BaseHTTPRequestHandler):

    protocol_version = "HTTP/1.1"

    def do_POST(self):
        self.server.num_requests += 1

        # Agent with AppSec disabled returns 405
        self.send_error(405)
        self.end_headers()


class UnavailableAppSecHandler(server.BaseHTTPRequestHandler):

    protocol_version = "HTTP/1.1"

    def do_POST(self):
        self.server.num_requests += 1
        self.send_error(502)
        self.end_headers()


class HTTPEventWriterTestCase(unittest.TestCase):
    def setUp(self):
        port = random.randint(25252, 32323)
        self.fake_server = server.HTTPServer(("localhost", port), FakeAppSecHandler)
        self.fake_server_url = "http://localhost:{}/".format(port)
        self.fake_server_thread = threading.Thread(
            target=functools.partial(self.fake_server.serve_forever, poll_interval=0.05)
        )
        self.fake_server.num_requests = 0
        self.fake_server.exceptions = []
        self.fake_server.payloads = []
        self.fake_server_thread.start()
        self.fake_dogstatsd = mock.Mock()
        # Wait for the server to be ready
        while getattr(self.fake_server, "fileno", None) is None:
            time.sleep(0.05)

    def stop_fake_server(self):
        try:
            self.fake_server.shutdown()
            self.fake_server_thread.join()
        except Exception:
            pass

    def get_writer(self):
        return HTTPEventWriter(url=self.fake_server_url, dogstatsd=self.fake_dogstatsd)

    def tearDown(self):
        self.stop_fake_server()

    def assert_no_exceptions(self):
        try:
            exc = self.fake_server.exceptions.pop()
        except IndexError:
            pass
        else:
            six.reraise(*exc)

    def test_write(self):
        w = self.get_writer()

        # first is accepted, second is skipped because it cannot be encoded
        ret = w.write([FakeEvent(), None])
        self.assertIsNone(ret)

        ret = w.flush()
        self.assertIsNone(ret)

        self.stop_fake_server()
        self.assert_no_exceptions()
        self.assertEqual(self.fake_server.num_requests, 1)
        self.assertEqual(len(self.fake_server.payloads), 1)

        self.fake_dogstatsd.distribution.assert_has_calls(
            [
                mock.call("datadog.tracer.appsec.buffer.accepted.events", 1, tags=[]),
                mock.call("datadog.tracer.appsec.buffer.dropped.events", 1, tags=["reason:encoding"]),
                mock.call("datadog.tracer.appsec.http.sent.events", 1, tags=[]),
                mock.call("datadog.tracer.appsec.http.requests", 1, tags=[]),
            ],
            any_order=True,
        )

    def test_api_key(self):
        class FakeAPIKey(FakeAppSecHandler):
            def do_POST(self):
                assert self.headers["DD-API-KEY"] == "XXX-XXX"
                FakeAppSecHandler.do_POST(self)

        self.fake_server.RequestHandlerClass = FakeAPIKey
        w = HTTPEventWriter(url=self.fake_server_url, api_key="XXX-XXX")
        w.write([FakeEvent()])
        w.flush()

        self.stop_fake_server()
        self.assert_no_exceptions()
        self.assertEqual(self.fake_server.num_requests, 1)
        self.assertEqual(len(self.fake_server.payloads), 1)

    def test_flush_empty(self):
        w = self.get_writer()
        w.flush()

        self.stop_fake_server()
        self.assert_no_exceptions()
        self.assertEqual(self.fake_server.num_requests, 0)

        self.fake_dogstatsd.distribution.assert_not_called()

    def test_not_supported(self):
        self.fake_server.RequestHandlerClass = IncorrectVersionHandler

        w = self.get_writer()
        w.write([FakeEvent()])
        w.flush()

        self.stop_fake_server()
        self.assert_no_exceptions()
        self.assertEqual(self.fake_server.num_requests, 1)
        self.assertEqual(len(self.fake_server.payloads), 0)

        self.fake_dogstatsd.distribution.assert_has_calls(
            [
                mock.call("datadog.tracer.appsec.buffer.accepted.events", 1, tags=[]),
                mock.call("datadog.tracer.appsec.http.requests", 1, tags=[]),
                mock.call("datadog.tracer.appsec.http.errors", 1, tags=["type:404", "type:err"]),
                mock.call("datadog.tracer.appsec.http.dropped.events", 1, tags=[]),
            ],
            any_order=True,
        )

    def test_appsec_disabled(self):
        self.fake_server.RequestHandlerClass = DisabledAppSecHandler

        w = self.get_writer()
        w.write([FakeEvent()])
        w.flush()

        self.stop_fake_server()
        self.assert_no_exceptions()
        self.assertEqual(self.fake_server.num_requests, 1)
        self.assertEqual(len(self.fake_server.payloads), 0)

        self.fake_dogstatsd.distribution.assert_has_calls(
            [
                mock.call("datadog.tracer.appsec.buffer.accepted.events", 1, tags=[]),
                mock.call("datadog.tracer.appsec.http.requests", 1, tags=[]),
                mock.call("datadog.tracer.appsec.http.errors", 1, tags=["type:405", "type:err"]),
                mock.call("datadog.tracer.appsec.http.dropped.events", 1, tags=[]),
            ],
            any_order=True,
        )

    def test_retry_unavailable(self):
        self.fake_server.RequestHandlerClass = UnavailableAppSecHandler

        w = self.get_writer()
        w.write([FakeEvent()])
        w.flush()

        self.stop_fake_server()
        self.assert_no_exceptions()
        self.assertEqual(self.fake_server.num_requests, 3)
        self.assertEqual(len(self.fake_server.payloads), 0)

        self.fake_dogstatsd.distribution.assert_has_calls(
            [
                mock.call("datadog.tracer.appsec.buffer.accepted.events", 1, tags=[]),
                mock.call("datadog.tracer.appsec.http.requests", 3, tags=[]),
                mock.call(
                    "datadog.tracer.appsec.http.errors", 3, tags=["type:502", "type:502", "type:502", "type:err"]
                ),
                mock.call("datadog.tracer.appsec.http.dropped.events", 1, tags=[]),
            ],
            any_order=True,
        )

    def test_appsec_event_batch(self):
        w = self.get_writer()
        w.write([FakeEvent()])
        w.flush()

        self.stop_fake_server()
        self.assert_no_exceptions()
        self.assertEqual(self.fake_server.num_requests, 1)
        self.assertEqual(len(self.fake_server.payloads), 1)
        data = json.loads(compat.to_unicode(self.fake_server.payloads[0]))
        self.assertEqual(
            data,
            {
                "protocol_version": 1,
                "idempotency_key": mock.ANY,
                "events": [{"foo": "bar"}],
            },
        )

    def test_payload_size(self):
        w = HTTPEventWriter(url=self.fake_server_url, max_payload_size=512, dogstatsd=self.fake_dogstatsd)
        # First event is ignored because too large
        w.write([FakeEvent("A" * 1024)])
        # Second event is accepted but third is rejected because buffer is full
        w.write([FakeEvent("A" * 256)] * 2)
        w.flush()

        self.stop_fake_server()
        self.assert_no_exceptions()
        self.assertEqual(self.fake_server.num_requests, 1)
        self.assertEqual(len(self.fake_server.payloads), 1)

        self.fake_dogstatsd.distribution.assert_has_calls(
            [
                mock.call("datadog.tracer.appsec.buffer.accepted.events", 1, tags=[]),
                mock.call("datadog.tracer.appsec.buffer.dropped.events", 2, tags=["reason:too_big", "reason:full"]),
                mock.call("datadog.tracer.appsec.http.requests", 1, tags=[]),
            ],
            any_order=True,
        )

    def test_fake_fork(self):
        w = self.get_writer()
        w.write([FakeEvent()])

        # fake fork
        w._reset()

        w.flush()

        self.stop_fake_server()
        self.assert_no_exceptions()
        self.assertEqual(self.fake_server.num_requests, 0)
        self.fake_dogstatsd.assert_not_called()
