from ddtrace.contrib.flask.patch import flask_version
from ddtrace.ext import http
from tests.utils import assert_span_http_status_code

from . import BaseFlaskTestCase


REMOVED_SPANS_2_2_0 = 1 if flask_version >= (2, 2, 0) else 0


class FlaskStaticFileTestCase(BaseFlaskTestCase):
    def test_serve_static_file(self):
        """
        When fetching a static file
            We create the expected spans
        """
        # DEV: By default a static handler for `./static/` is configured for us
        res = self.client.get("/static/test.txt")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, b"Hello Flask\n")

        spans = self.get_spans()
        self.assertEqual(len(spans), 11 - REMOVED_SPANS_2_2_0)

        req_span = self.find_span_by_name(spans, "flask.request")
        handler_span = self.find_span_by_name(spans, "static")
        send_file_span = self.find_span_by_name(spans, "flask.send_static_file")

        # flask.request span
        self.assertEqual(req_span.error, 0)
        self.assertEqual(req_span.service, "flask")
        self.assertEqual(req_span.name, "flask.request")
        self.assertEqual(req_span.resource, "GET /static/<path:filename>")
        self.assertEqual(req_span.get_tag("flask.endpoint"), "static")
        self.assertEqual(req_span.get_tag("flask.url_rule"), "/static/<path:filename>")
        self.assertEqual(req_span.get_tag("flask.view_args.filename"), "test.txt")
        assert_span_http_status_code(req_span, 200)
        self.assertEqual(req_span.get_tag(http.URL), "http://localhost/static/test.txt")
        self.assertEqual(req_span.get_tag("http.method"), "GET")

        # static span
        self.assertEqual(handler_span.error, 0)
        self.assertEqual(handler_span.service, "flask")
        self.assertEqual(handler_span.name, "static")
        self.assertEqual(handler_span.resource, "/static/<path:filename>")

        # flask.send_static_file span
        self.assertEqual(send_file_span.error, 0)
        self.assertEqual(send_file_span.service, "flask")
        self.assertEqual(send_file_span.name, "flask.send_static_file")
        self.assertEqual(send_file_span.resource, "flask.send_static_file")

    def test_serve_static_file_404(self):
        """
        When fetching a static file
            When the file does not exist
                We create the expected spans
        """
        # DEV: By default a static handler for `./static/` is configured for us
        res = self.client.get("/static/unknown-file")
        self.assertEqual(res.status_code, 404)
        # Read response data from the test client to close flask.request and flask.response spans
        self.assertIsNotNone(res.data)

        spans = self.get_spans()
        self.assertEqual(len(spans), 13 - REMOVED_SPANS_2_2_0)

        req_span = self.find_span_by_name(spans, "flask.request")
        handler_span = self.find_span_by_name(spans, "static")
        send_file_span = self.find_span_by_name(spans, "flask.send_static_file")

        # flask.request span
        self.assertEqual(req_span.error, 0)
        self.assertEqual(req_span.service, "flask")
        self.assertEqual(req_span.name, "flask.request")
        self.assertEqual(req_span.resource, "GET /static/<path:filename>")
        self.assertEqual(req_span.get_tag("flask.endpoint"), "static")
        self.assertEqual(req_span.get_tag("flask.url_rule"), "/static/<path:filename>")
        self.assertEqual(req_span.get_tag("flask.view_args.filename"), "unknown-file")
        assert_span_http_status_code(req_span, 404)
        self.assertEqual(req_span.get_tag(http.URL), "http://localhost/static/unknown-file")
        self.assertEqual(req_span.get_tag("http.method"), "GET")

        # static span
        self.assertEqual(handler_span.error, 1)
        self.assertEqual(handler_span.service, "flask")
        self.assertEqual(handler_span.name, "static")
        self.assertEqual(handler_span.resource, "/static/<path:filename>")
        self.assertTrue(handler_span.get_tag("error.msg").startswith("404 Not Found"))
        self.assertTrue(handler_span.get_tag("error.stack").startswith("Traceback"))
        self.assertEqual(handler_span.get_tag("error.type"), "werkzeug.exceptions.NotFound")

        # flask.send_static_file span
        self.assertEqual(send_file_span.error, 1)
        self.assertEqual(send_file_span.service, "flask")
        self.assertEqual(send_file_span.name, "flask.send_static_file")
        self.assertEqual(send_file_span.resource, "flask.send_static_file")
        self.assertTrue(send_file_span.get_tag("error.msg").startswith("404 Not Found"))
        self.assertTrue(send_file_span.get_tag("error.stack").startswith("Traceback"))
        self.assertEqual(send_file_span.get_tag("error.type"), "werkzeug.exceptions.NotFound")
