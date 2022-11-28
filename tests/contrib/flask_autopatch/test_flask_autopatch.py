# -*- coding: utf-8 -*-
import flask

from ddtrace import Pin
from ddtrace.contrib.flask.patch import flask_version
from ddtrace.ext import http
from ddtrace.vendor import wrapt
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured
from tests.utils import assert_span_http_status_code


REMOVED_SPANS_2_2_0 = 1 if flask_version >= (2, 2, 0) else 0


class FlaskAutopatchTestCase(TracerTestCase):
    def setUp(self):
        super(FlaskAutopatchTestCase, self).setUp()
        self.app = flask.Flask(__name__)
        Pin.override(self.app, service="test-flask", tracer=self.tracer)
        self.client = self.app.test_client()

    def test_patched(self):
        """
        When using ddtrace-run
            Then the `flask` module is patched
        """
        # DEV: We have great test coverage in tests.contrib.flask,
        #      we only need basic tests here to assert `ddtrace-run` patched thingsa

        # Assert module is marked as patched
        self.assertTrue(flask._datadog_patch)

        # Assert our instance of flask.app.Flask is patched
        self.assertTrue(isinstance(self.app.add_url_rule, wrapt.ObjectProxy))
        self.assertTrue(isinstance(self.app.wsgi_app, wrapt.ObjectProxy))

        # Assert the base module flask.app.Flask methods are patched
        self.assertTrue(isinstance(flask.app.Flask.add_url_rule, wrapt.ObjectProxy))
        self.assertTrue(isinstance(flask.app.Flask.wsgi_app, wrapt.ObjectProxy))

    def test_request(self):
        """
        When using ddtrace-run
            When making a request to flask app
                We generate the expected spans
        """

        @self.app.route("/")
        def index():
            return "Hello Flask", 200

        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, b"Hello Flask")

        spans = self.pop_spans()
        self.assertEqual(len(spans), 10 - REMOVED_SPANS_2_2_0)

        expected_spans = [
            "flask.request",
            "flask.application",
            "flask.try_trigger_before_first_request_functions",
            "flask.preprocess_request",
            "flask.dispatch_request",
            "tests.contrib.flask_autopatch.test_flask_autopatch.index",
            "flask.process_response",
            "flask.do_teardown_request",
            "flask.do_teardown_appcontext",
            "flask.response",
        ]
        if flask_version >= (2, 2, 0):
            expected_spans = [
                "flask.request",
                "flask.application",
                "flask.preprocess_request",
                "flask.dispatch_request",
                "tests.contrib.flask_autopatch.test_flask_autopatch.index",
                "flask.process_response",
                "flask.do_teardown_request",
                "flask.do_teardown_appcontext",
                "flask.response",
            ]

        self.assertListEqual(
            expected_spans,
            [s.name for s in spans],
        )

        # Assert span services
        for span in spans:
            self.assertEqual(span.service, "test-flask")

        # Root request span
        req_span = spans[0]
        assert_is_measured(req_span)
        self.assertEqual(req_span.service, "test-flask")
        self.assertEqual(req_span.name, "flask.request")
        self.assertEqual(req_span.resource, "GET /")
        self.assertEqual(req_span.span_type, "web")
        self.assertEqual(req_span.error, 0)
        self.assertIsNone(req_span.parent_id)

        # Request tags
        for tag in ["flask.version", "http.url", "http.method", "http.status_code", "flask.endpoint", "flask.url_rule"]:
            assert tag in req_span.get_tags()

        self.assertEqual(req_span.get_tag("flask.endpoint"), "index")
        self.assertEqual(req_span.get_tag("flask.url_rule"), "/")
        self.assertEqual(req_span.get_tag("http.method"), "GET")
        self.assertEqual(req_span.get_tag(http.URL), "http://localhost/")
        self.assertEqual(req_span.get_tag("component"), "flask")
        assert_span_http_status_code(req_span, 200)

        # Handler span
        handler_span = spans[5]
        self.assertEqual(handler_span.service, "test-flask")

        expected_span_name = (
            "flask.process_response"
            if flask_version >= (2, 2, 0)
            else "tests.contrib.flask_autopatch.test_flask_autopatch.index"
        )
        self.assertEqual(handler_span.name, expected_span_name)
        expected_span_resource = "flask.process_response" if flask_version >= (2, 2, 0) else "/"
        self.assertEqual(handler_span.resource, expected_span_resource)
        self.assertEqual(req_span.error, 0)
