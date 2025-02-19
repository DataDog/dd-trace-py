# -*- coding: utf-8 -*-
import json
import os
import subprocess
import sys
import time

import flask
from flask import abort
from flask import jsonify
from flask import make_response
import pytest

from ddtrace.constants import ERROR_MSG
from ddtrace.constants import USER_KEEP
from ddtrace.contrib.internal.flask.patch import flask_version
from ddtrace.ext import http
from ddtrace.propagation.http import HTTP_HEADER_PARENT_ID
from ddtrace.propagation.http import HTTP_HEADER_TRACE_ID
from tests.conftest import DEFAULT_DDTRACE_SUBPROCESS_TEST_SERVICE_NAME
from tests.tracer.utils_inferred_spans.test_helpers import assert_web_and_inferred_aws_api_gateway_span_data
from tests.utils import assert_is_measured
from tests.utils import assert_span_http_status_code

from . import BaseFlaskTestCase


REMOVED_SPANS_2_2_0 = 1 if flask_version >= (2, 2, 0) else 0
SIGTERM_EXIT_CODE = -15


base_exception_name = "builtins.Exception"


class FlaskRequestTestCase(BaseFlaskTestCase):
    def test_request(self):
        """
        When making a request
            We create the expected spans
        """

        @self.app.route("/")
        def index():
            return "Hello Flask", 200

        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, b"Hello Flask")

        spans = self.get_spans()
        self.assertEqual(len(spans), 10 - REMOVED_SPANS_2_2_0)

        # Assert the order of the spans created
        expected_spans = [
            "flask.request",
            "flask.application",
            "flask.try_trigger_before_first_request_functions",
            "flask.preprocess_request",
            "flask.dispatch_request",
            "tests.contrib.flask.test_request.index",
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
                "tests.contrib.flask.test_request.index",
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
            self.assertEqual(span.service, "flask")

        # Root request span
        req_span = spans[0]
        assert_is_measured(req_span)
        self.assertEqual(req_span.service, "flask")
        self.assertEqual(req_span.name, "flask.request")
        self.assertEqual(req_span.resource, "GET /")
        self.assertEqual(req_span.span_type, "web")
        self.assertEqual(req_span.error, 0)
        self.assertIsNone(req_span.parent_id)

        # Request tags
        self.assertEqual(req_span.get_tag("flask.endpoint"), "index")
        self.assertEqual(req_span.get_tag("flask.url_rule"), "/")
        self.assertEqual(req_span.get_tag("http.method"), "GET")
        self.assertEqual(req_span.get_tag(http.URL), "http://localhost/")
        assert_span_http_status_code(req_span, 200)
        assert http.QUERY_STRING not in req_span.get_tags()

        # Handler span
        handler_span = spans[5 - REMOVED_SPANS_2_2_0]
        self.assertEqual(handler_span.service, "flask")
        self.assertEqual(handler_span.name, "tests.contrib.flask.test_request.index")
        self.assertEqual(handler_span.resource, "/")
        self.assertEqual(req_span.error, 0)

    def test_route_params_request(self):
        """
        When making a request to an endpoint with non-string url params
            We create the expected spans
        """

        @self.app.route("/route_params/<first>/<int:second>/<float:third>/<path:fourth>")
        def route_params(first, second, third, fourth):
            return jsonify(
                {
                    "first": first,
                    "second": second,
                    "third": third,
                    "fourth": fourth,
                }
            )

        res = self.client.get("/route_params/test/100/5.5/some/sub/path")
        self.assertEqual(res.status_code, 200)
        if isinstance(res.data, bytes):
            data = json.loads(res.data.decode())
        else:
            data = json.loads(res.data)

        assert data == {
            "first": "test",
            "second": 100,
            "third": 5.5,
            "fourth": "some/sub/path",
        }

        spans = self.get_spans()
        self.assertEqual(len(spans), 10 - REMOVED_SPANS_2_2_0)

        root = spans[0]
        assert root.name == "flask.request"
        assert root.get_tag(http.URL) == "http://localhost/route_params/test/100/5.5/some/sub/path"
        assert root.get_tag(http.ROUTE) == "/route_params/<first>/<int:second>/<float:third>/<path:fourth>"
        assert root.get_tag("flask.endpoint") == "route_params"
        assert root.get_tag("flask.url_rule") == "/route_params/<first>/<int:second>/<float:third>/<path:fourth>"
        assert root.get_tag("flask.view_args.first") == "test"
        assert root.get_metric("flask.view_args.second") == 100.0
        assert root.get_metric("flask.view_args.third") == 5.5
        assert root.get_tag("flask.view_args.fourth") == "some/sub/path"

    def test_request_query_string_trace(self):
        """Make sure when making a request that we create the expected spans and capture the query string."""

        @self.app.route("/")
        def index():
            return "Hello Flask", 200

        with self.override_http_config("flask", dict(trace_query_string=True)):
            self.client.get("/?foo=bar&baz=biz")
        spans = self.get_spans()

        # Request tags
        assert spans[0].get_tag(http.QUERY_STRING) == "foo=bar&baz=biz"

    @pytest.mark.skipif(flask_version >= (2, 0, 0), reason="Decoding error thrown in url_split")
    def test_request_query_string_trace_encoding(self):
        """Make sure when making a request that we create the expected spans and capture the query string
        with a non-UTF-8 encoding.
        """

        @self.app.route("/")
        def index():
            return "Hello Flask", 200

        with self.override_http_config("flask", dict(trace_query_string=True)):
            self.client.get("/?foo=bar&baz=정상처리".encode("euc-kr"))
        spans = self.get_spans()

        # Request tags
        assert spans[0].get_tag(http.QUERY_STRING) == "foo=bar&baz=����ó��"

    def test_distributed_tracing(self):
        """
        When making a request
            When distributed tracing headers are present
                We create the expected spans
        """

        @self.app.route("/")
        def index():
            return "Hello Flask", 200

        # Default: distributed tracing enabled
        res = self.client.get(
            "/",
            headers={
                HTTP_HEADER_PARENT_ID: "12345",
                HTTP_HEADER_TRACE_ID: "678910",
            },
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, b"Hello Flask")

        # Assert parent and trace id are properly set on the root span
        span = self.find_span_by_name(self.get_spans(), "flask.request")
        self.assertEqual(span.trace_id, 678910)
        self.assertEqual(span.parent_id, 12345)

        # Explicitly enable distributed tracing
        with self.override_config("flask", dict(distributed_tracing_enabled=True)):
            res = self.client.get(
                "/",
                headers={
                    HTTP_HEADER_PARENT_ID: "12345",
                    HTTP_HEADER_TRACE_ID: "678910",
                },
            )
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.data, b"Hello Flask")

        # Assert parent and trace id are properly set on the root span
        span = self.find_span_by_name(self.get_spans(), "flask.request")
        self.assertEqual(span.trace_id, 678910)
        self.assertEqual(span.parent_id, 12345)

        # With distributed tracing disabled
        with self.override_config("flask", dict(distributed_tracing_enabled=False)):
            res = self.client.get(
                "/",
                headers={
                    HTTP_HEADER_PARENT_ID: "12345",
                    HTTP_HEADER_TRACE_ID: "678910",
                },
            )
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.data, b"Hello Flask")

        # Assert parent and trace id are properly set on the root span
        span = self.find_span_by_name(self.get_spans(), "flask.request")
        self.assertNotEqual(span.trace_id, 678910)
        self.assertIsNone(span.parent_id)

    def test_inferred_spans_api_gateway_default(self):
        """
        When making a request starting from AWS API Gateway
            We create an inferred span with the headers
        """

        @self.app.route("/")
        def index():
            return "Hello Flask", 200

        @self.app.route("/applicationerror")
        def exception_endpoint():
            raise Exception("Application error")

        @self.app.route("/returnerrorcode")
        def error_status_code():
            return "Endpoint failed", 599

        # By default, no inferred spans should be created

        _ = self.client.get("/")

        # Assert that without the feature, Flask is the root
        span = self.find_span_by_name(self.get_spans(), "flask.request")
        self.assertEqual(span.parent_id, None)

        # With the inferred spans enabled
        with self.override_global_config(dict(_inferred_proxy_services_enabled="true")):
            # A request without headers has no inferred span
            self.reset()
            _ = self.client.get("/")

            web_span = self.find_span_by_name(self.get_spans(), "flask.request")
            aws_gateway_span = web_span._parent

            assert aws_gateway_span is None

            test_headers = {
                "x-dd-proxy": "aws-apigateway",
                "x-dd-proxy-request-time-ms": "1736973768000",
                "x-dd-proxy-path": "/",
                "x-dd-proxy-httpmethod": "GET",
                "x-dd-proxy-domain-name": "local",
                "x-dd-proxy-stage": "stage",
            }

            self.reset()
            _ = self.client.get("/", headers=test_headers)

            web_span = self.find_span_by_name(self.get_spans(), "flask.request")
            aws_gateway_span = web_span._parent

            assert_web_and_inferred_aws_api_gateway_span_data(
                aws_gateway_span,
                web_span,
                web_span_name="flask.request",
                web_span_component="flask",
                web_span_service_name="flask",
                web_span_resource="GET /",
                api_gateway_service_name="local",
                api_gateway_resource="GET /",
                method="GET",
                status_code="200",
                url="local/",
                start=1736973768,
            )

            # When hitting an application error, we extract the status and errors
            self.reset()
            _ = self.client.get("/applicationerror", headers=test_headers)
            web_span = self.find_span_by_name(self.get_spans(), "flask.request")
            aws_gateway_span = web_span._parent

            assert_web_and_inferred_aws_api_gateway_span_data(
                aws_gateway_span,
                web_span,
                web_span_name="flask.request",
                web_span_component="flask",
                web_span_service_name="flask",
                web_span_resource="GET /applicationerror",
                api_gateway_service_name="local",
                api_gateway_resource="GET /",
                method="GET",
                status_code="500",
                url="local/",
                start=1736973768,
            )

            # When hitting an endpoint with a custom status code, we report the code
            self.reset()
            _ = self.client.get("/returnerrorcode", headers=test_headers)
            web_span = self.find_span_by_name(self.get_spans(), "flask.request")
            aws_gateway_span = web_span._parent

            assert_web_and_inferred_aws_api_gateway_span_data(
                aws_gateway_span,
                web_span,
                web_span_name="flask.request",
                web_span_component="flask",
                web_span_service_name="flask",
                web_span_resource="GET /returnerrorcode",
                api_gateway_service_name="local",
                api_gateway_resource="GET /",
                method="GET",
                status_code="599",
                url="local/",
                start=1736973768,
            )

    def test_inferred_spans_api_gateway_distributed_tracing(self):
        """
        When making a request starting from AWS API Gateway
            We create an inferred span with the headers when Datadog headers are passed
        """

        @self.app.route("/")
        def index():
            return "Hello Flask", 200

        test_headers = {
            "x-dd-proxy": "aws-apigateway",
            "x-dd-proxy-request-time-ms": "1736973768000",
            "x-dd-proxy-path": "/",
            "x-dd-proxy-httpmethod": "GET",
            "x-dd-proxy-domain-name": "local",
            "x-dd-proxy-stage": "stage",
            "x-datadog-trace-id": "1",
            "x-datadog-parent-id": "2",
            "x-datadog-origin": "rum",
            "x-datadog-sampling-priority": "2",
        }

        # Without the feature enabled, we should not be creating the inferred span
        self.client.get("/", headers=test_headers)
        web_span = self.find_span_by_name(self.get_spans(), "flask.request")
        aws_gateway_span = web_span._parent
        assert aws_gateway_span is None
        assert web_span.parent_id == 2
        assert web_span.trace_id == 1

        # With the feature enabled
        with self.override_global_config(dict(_inferred_proxy_services_enabled="true")):
            self.reset()
            self.client.get("/", headers=test_headers)
            web_span = self.find_span_by_name(self.get_spans(), "flask.request")
            aws_gateway_span = web_span._parent

            # Assert common behavior including aws gateway metadata
            assert_web_and_inferred_aws_api_gateway_span_data(
                aws_gateway_span,
                web_span,
                web_span_name="flask.request",
                web_span_component="flask",
                web_span_service_name="flask",
                web_span_resource="GET /",
                api_gateway_service_name="local",
                api_gateway_resource="GET /",
                method="GET",
                status_code="200",
                url="local/",
                start=1736973768,
                is_distributed=True,
                distributed_trace_id=1,
                distributed_parent_id=2,
                distributed_sampling_priority=USER_KEEP,
            )

    def test_request_query_string(self):
        """
        When making a request
            When the request contains a query string
                We create the expected spans
        """

        @self.app.route("/")
        def index():
            return "Hello Flask", 200

        res = self.client.get("/", query_string=dict(token="flask"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, b"Hello Flask")

        spans = self.get_spans()
        self.assertEqual(len(spans), 10 - REMOVED_SPANS_2_2_0)

        # Assert the order of the spans created
        expected_spans = [
            "flask.request",
            "flask.application",
            "flask.try_trigger_before_first_request_functions",
            "flask.preprocess_request",
            "flask.dispatch_request",
            "tests.contrib.flask.test_request.index",
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
                "tests.contrib.flask.test_request.index",
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
            self.assertEqual(span.service, "flask")

        # Root request span
        req_span = spans[0]

        assert_is_measured(req_span)
        self.assertEqual(req_span.service, "flask")
        self.assertEqual(req_span.name, "flask.request")
        # Note: contains no query string
        self.assertEqual(req_span.resource, "GET /")
        self.assertEqual(req_span.span_type, "web")
        self.assertEqual(req_span.error, 0)
        self.assertIsNone(req_span.parent_id)

        # Request tags
        self.assertEqual(req_span.get_tag("flask.endpoint"), "index")
        # Note: contains no query string
        self.assertEqual(req_span.get_tag("flask.url_rule"), "/")
        self.assertEqual(req_span.get_tag("http.method"), "GET")
        # Note: contains query string (possibly redacted)
        self.assertEqual(req_span.get_tag(http.URL), "http://localhost/?<redacted>")
        assert_span_http_status_code(req_span, 200)

        # Handler span
        handler_span = spans[5 - REMOVED_SPANS_2_2_0]
        self.assertEqual(handler_span.service, "flask")
        self.assertEqual(handler_span.name, "tests.contrib.flask.test_request.index")
        # Note: contains no query string
        self.assertEqual(handler_span.resource, "/")
        self.assertEqual(req_span.error, 0)

    def test_request_unicode(self):
        """
        When making a request
            When the url contains unicode
                We create the expected spans
        """

        @self.app.route("/üŋïĉóđē")
        def unicode():
            return "üŋïĉóđē", 200

        res = self.client.get("/üŋïĉóđē")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, b"\xc3\xbc\xc5\x8b\xc3\xaf\xc4\x89\xc3\xb3\xc4\x91\xc4\x93")

        spans = self.get_spans()
        self.assertEqual(len(spans), 10 - REMOVED_SPANS_2_2_0)

        # Assert the order of the spans created
        expected_spans = [
            "flask.request",
            "flask.application",
            "flask.try_trigger_before_first_request_functions",
            "flask.preprocess_request",
            "flask.dispatch_request",
            "tests.contrib.flask.test_request.unicode",
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
                "tests.contrib.flask.test_request.unicode",
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
            self.assertEqual(span.service, "flask")

        # Root request span
        req_span = spans[0]
        self.assertEqual(req_span.service, "flask")
        self.assertEqual(req_span.name, "flask.request")
        self.assertEqual(req_span.resource, "GET /üŋïĉóđē")
        self.assertEqual(req_span.span_type, "web")
        self.assertEqual(req_span.error, 0)
        self.assertIsNone(req_span.parent_id)

        # Request tags
        self.assertEqual(req_span.get_tag("flask.endpoint"), "unicode")
        self.assertEqual(req_span.get_tag("flask.url_rule"), "/üŋïĉóđē")
        self.assertEqual(req_span.get_tag("http.method"), "GET")
        self.assertEqual(req_span.get_tag(http.URL), "http://localhost/üŋïĉóđē")
        assert_span_http_status_code(req_span, 200)

        # Handler span
        handler_span = spans[5 - REMOVED_SPANS_2_2_0]
        self.assertEqual(handler_span.service, "flask")
        self.assertEqual(handler_span.name, "tests.contrib.flask.test_request.unicode")
        self.assertEqual(handler_span.resource, "/üŋïĉóđē")
        self.assertEqual(req_span.error, 0)

    def test_request_404(self):
        """
        When making a request
            When the requested endpoint was not found
                We create the expected spans
        """
        res = self.client.get("/not-found")
        self.assertEqual(res.status_code, 404)

        spans = self.get_spans()
        self.assertEqual(len(spans), 11 - REMOVED_SPANS_2_2_0)

        # Assert the order of the spans created
        expected_spans = [
            "flask.request",
            "flask.application",
            "flask.try_trigger_before_first_request_functions",
            "flask.preprocess_request",
            "flask.dispatch_request",
            "flask.handle_user_exception",
            "flask.handle_http_exception",
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
                "flask.handle_user_exception",
                "flask.handle_http_exception",
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
            self.assertEqual(span.service, "flask")

        # Root request span
        req_span = spans[0]
        assert_is_measured(req_span)
        self.assertEqual(req_span.service, "flask")
        self.assertEqual(req_span.name, "flask.request")
        self.assertEqual(req_span.resource, "GET 404")
        self.assertEqual(req_span.span_type, "web")
        self.assertEqual(req_span.error, 0)
        self.assertIsNone(req_span.parent_id)

        # Request tags
        self.assertEqual(req_span.get_tag("http.method"), "GET")
        self.assertEqual(req_span.get_tag(http.URL), "http://localhost/not-found")
        assert_span_http_status_code(req_span, 404)

        # Dispatch span
        dispatch_span = spans[4 - REMOVED_SPANS_2_2_0]
        self.assertEqual(dispatch_span.service, "flask")
        self.assertEqual(dispatch_span.name, "flask.dispatch_request")
        self.assertEqual(dispatch_span.resource, "flask.dispatch_request")
        self.assertEqual(dispatch_span.error, 0)
        self.assertIsNone(dispatch_span.get_tag(ERROR_MSG))
        self.assertIsNone(dispatch_span.get_tag("error.stack"))
        self.assertIsNone(dispatch_span.get_tag("error.type"))

    def test_request_abort_404(self):
        """
        When making a request
            When the requested endpoint calls `abort(404)`
                We create the expected spans
        """

        @self.app.route("/not-found")
        def not_found():
            abort(404)

        res = self.client.get("/not-found")
        self.assertEqual(res.status_code, 404)

        spans = self.get_spans()
        self.assertEqual(len(spans), 12 - REMOVED_SPANS_2_2_0)

        # Assert the order of the spans created
        expected_spans = [
            "flask.request",
            "flask.application",
            "flask.try_trigger_before_first_request_functions",
            "flask.preprocess_request",
            "flask.dispatch_request",
            "tests.contrib.flask.test_request.not_found",
            "flask.handle_user_exception",
            "flask.handle_http_exception",
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
                "tests.contrib.flask.test_request.not_found",
                "flask.handle_user_exception",
                "flask.handle_http_exception",
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
            self.assertEqual(span.service, "flask")

        # Root request span
        req_span = spans[0]

        assert_is_measured(req_span)
        self.assertEqual(req_span.service, "flask")
        self.assertEqual(req_span.name, "flask.request")
        self.assertEqual(req_span.resource, "GET /not-found")
        self.assertEqual(req_span.span_type, "web")
        self.assertEqual(req_span.error, 0)
        self.assertIsNone(req_span.parent_id)

        # Request tags
        self.assertEqual(req_span.get_tag("http.method"), "GET")
        self.assertEqual(req_span.get_tag(http.URL), "http://localhost/not-found")
        assert_span_http_status_code(req_span, 404)
        self.assertEqual(req_span.get_tag("flask.endpoint"), "not_found")
        self.assertEqual(req_span.get_tag("flask.url_rule"), "/not-found")

        # Dispatch span
        dispatch_span = spans[4 - REMOVED_SPANS_2_2_0]
        self.assertEqual(dispatch_span.service, "flask")
        self.assertEqual(dispatch_span.name, "flask.dispatch_request")
        self.assertEqual(dispatch_span.resource, "flask.dispatch_request")
        self.assertEqual(dispatch_span.error, 0)
        self.assertIsNone(dispatch_span.get_tag(ERROR_MSG))
        self.assertIsNone(dispatch_span.get_tag("error.stack"))
        self.assertIsNone(dispatch_span.get_tag("error.type"))

        # Handler span
        handler_span = spans[5 - REMOVED_SPANS_2_2_0]
        self.assertEqual(handler_span.service, "flask")
        self.assertEqual(handler_span.name, "tests.contrib.flask.test_request.not_found")
        self.assertEqual(handler_span.resource, "/not-found")
        self.assertEqual(handler_span.error, 1)
        self.assertTrue(handler_span.get_tag(ERROR_MSG).startswith("404 Not Found"))
        self.assertTrue(handler_span.get_tag("error.stack").startswith("Traceback"))
        self.assertEqual(handler_span.get_tag("error.type"), "werkzeug.exceptions.NotFound")

    def test_request_500(self):
        """
        When making a request
            When the requested endpoint raises an exception
                We create the expected spans
        """

        @self.app.route("/500")
        def fivehundred():
            raise Exception("500 error")

        res = self.client.get("/500")
        self.assertEqual(res.status_code, 500)

        spans = self.get_spans()

        expected_spans = [
            "flask.request",
            "flask.application",
            "flask.try_trigger_before_first_request_functions",
            "flask.preprocess_request",
            "flask.dispatch_request",
            "tests.contrib.flask.test_request.fivehundred",
            "flask.handle_user_exception",
            "flask.handle_exception",
        ]
        if flask_version >= (2, 2, 0):
            expected_spans = [
                "flask.request",
                "flask.application",
                "flask.preprocess_request",
                "flask.dispatch_request",
                "tests.contrib.flask.test_request.fivehundred",
                "flask.handle_user_exception",
                "flask.handle_exception",
            ]

        if not (flask.__version__.startswith("0.") or flask.__version__.startswith("1.0")):
            expected_spans.append("flask.process_response")

        expected_spans += [
            "flask.do_teardown_request",
            "flask.do_teardown_appcontext",
            "flask.response",
        ]

        # Assert the order of the spans created
        assert expected_spans == [s.name for s in spans]

        # Assert span services
        for span in spans:
            self.assertEqual(span.service, "flask")

        # Root request span
        req_span = spans[0]
        assert_is_measured(req_span)
        self.assertEqual(req_span.service, "flask")
        self.assertEqual(req_span.name, "flask.request")
        self.assertEqual(req_span.resource, "GET /500")
        self.assertEqual(req_span.span_type, "web")
        self.assertEqual(req_span.error, 1)
        self.assertIsNone(req_span.parent_id)

        # Request tags
        self.assertEqual(req_span.get_tag("http.method"), "GET")
        self.assertEqual(req_span.get_tag(http.URL), "http://localhost/500")
        assert_span_http_status_code(req_span, 500)
        self.assertEqual(req_span.get_tag("flask.endpoint"), "fivehundred")
        self.assertEqual(req_span.get_tag("flask.url_rule"), "/500")

        # Dispatch span
        dispatch_span = spans[4 - REMOVED_SPANS_2_2_0]
        self.assertEqual(dispatch_span.service, "flask")
        self.assertEqual(dispatch_span.name, "flask.dispatch_request")
        self.assertEqual(dispatch_span.resource, "flask.dispatch_request")
        self.assertEqual(dispatch_span.error, 1)
        self.assertTrue(dispatch_span.get_tag(ERROR_MSG).startswith("500 error"))
        self.assertTrue(dispatch_span.get_tag("error.stack").startswith("Traceback"))
        self.assertEqual(dispatch_span.get_tag("error.type"), base_exception_name)

        # Handler span
        handler_span = spans[5 - REMOVED_SPANS_2_2_0]
        self.assertEqual(handler_span.service, "flask")
        self.assertEqual(handler_span.name, "tests.contrib.flask.test_request.fivehundred")
        self.assertEqual(handler_span.resource, "/500")
        self.assertEqual(handler_span.error, 1)
        self.assertTrue(handler_span.get_tag(ERROR_MSG).startswith("500 error"))
        self.assertTrue(handler_span.get_tag("error.stack").startswith("Traceback"))
        self.assertEqual(handler_span.get_tag("error.type"), base_exception_name)

        # User exception span
        user_ex_span = spans[6 - REMOVED_SPANS_2_2_0]
        self.assertEqual(user_ex_span.service, "flask")
        self.assertEqual(user_ex_span.name, "flask.handle_user_exception")
        self.assertEqual(user_ex_span.resource, "flask.handle_user_exception")
        self.assertEqual(user_ex_span.error, 1)
        self.assertTrue(user_ex_span.get_tag(ERROR_MSG).startswith("500 error"))
        self.assertTrue(user_ex_span.get_tag("error.stack").startswith("Traceback"))
        self.assertEqual(user_ex_span.get_tag("error.type"), base_exception_name)

    def test_request_501(self):
        """
        When making a request
            When the requested endpoint calls `abort(501)`
                We create the expected spans
        """

        @self.app.route("/501")
        def fivehundredone():
            abort(501)

        res = self.client.get("/501")
        self.assertEqual(res.status_code, 501)

        spans = self.get_spans()
        self.assertEqual(len(spans), 12 - REMOVED_SPANS_2_2_0)

        # Assert the order of the spans created
        expected_spans = [
            "flask.request",
            "flask.application",
            "flask.try_trigger_before_first_request_functions",
            "flask.preprocess_request",
            "flask.dispatch_request",
            "tests.contrib.flask.test_request.fivehundredone",
            "flask.handle_user_exception",
            "flask.handle_http_exception",
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
                "tests.contrib.flask.test_request.fivehundredone",
                "flask.handle_user_exception",
                "flask.handle_http_exception",
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
            self.assertEqual(span.service, "flask")

        # Root request span
        req_span = spans[0]
        assert_is_measured(req_span)
        self.assertEqual(req_span.service, "flask")
        self.assertEqual(req_span.name, "flask.request")
        self.assertEqual(req_span.resource, "GET /501")
        self.assertEqual(req_span.span_type, "web")
        self.assertEqual(req_span.error, 1)
        self.assertIsNone(req_span.parent_id)

        # Request tags
        self.assertEqual(req_span.get_tag("http.method"), "GET")
        self.assertEqual(req_span.get_tag(http.URL), "http://localhost/501")
        assert_span_http_status_code(req_span, 501)
        self.assertEqual(req_span.get_tag("flask.endpoint"), "fivehundredone")
        self.assertEqual(req_span.get_tag("flask.url_rule"), "/501")

        # Dispatch span
        dispatch_span = spans[4 - REMOVED_SPANS_2_2_0]
        self.assertEqual(dispatch_span.service, "flask")
        self.assertEqual(dispatch_span.name, "flask.dispatch_request")
        self.assertEqual(dispatch_span.resource, "flask.dispatch_request")
        self.assertEqual(dispatch_span.error, 1)
        self.assertTrue(dispatch_span.get_tag(ERROR_MSG).startswith("501 Not Implemented"))
        self.assertTrue(dispatch_span.get_tag("error.stack").startswith("Traceback"))
        self.assertEqual(dispatch_span.get_tag("error.type"), "werkzeug.exceptions.NotImplemented")

        # Handler span
        handler_span = spans[5 - REMOVED_SPANS_2_2_0]
        self.assertEqual(handler_span.service, "flask")
        self.assertEqual(handler_span.name, "tests.contrib.flask.test_request.fivehundredone")
        self.assertEqual(handler_span.resource, "/501")
        self.assertEqual(handler_span.error, 1)
        self.assertTrue(handler_span.get_tag(ERROR_MSG).startswith("501 Not Implemented"))
        self.assertTrue(handler_span.get_tag("error.stack").startswith("Traceback"))
        self.assertEqual(handler_span.get_tag("error.type"), "werkzeug.exceptions.NotImplemented")

        # User exception span
        user_ex_span = spans[6 - REMOVED_SPANS_2_2_0]
        self.assertEqual(user_ex_span.service, "flask")
        self.assertEqual(user_ex_span.name, "flask.handle_user_exception")
        self.assertEqual(user_ex_span.resource, "flask.handle_user_exception")
        self.assertEqual(user_ex_span.error, 0)

    def test_request_error_handler(self):
        """
        When making a request
            When the requested endpoint raises an exception
                We create the expected spans
        """

        @self.app.errorhandler(500)
        def error_handler(e):
            return "Whoops", 500

        @self.app.route("/500")
        def fivehundred():
            raise Exception("500 error")

        res = self.client.get("/500")
        self.assertEqual(res.status_code, 500)
        self.assertEqual(res.data, b"Whoops")

        spans = self.get_spans()

        if flask_version >= (0, 12):
            self.assertEqual(len(spans), 13 - REMOVED_SPANS_2_2_0)

            # Assert the order of the spans created
            expected_spans = [
                "flask.request",
                "flask.application",
                "flask.try_trigger_before_first_request_functions",
                "flask.preprocess_request",
                "flask.dispatch_request",
                "tests.contrib.flask.test_request.fivehundred",
                "flask.handle_user_exception",
                "flask.handle_exception",
                "tests.contrib.flask.test_request.error_handler",
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
                    "tests.contrib.flask.test_request.fivehundred",
                    "flask.handle_user_exception",
                    "flask.handle_exception",
                    "tests.contrib.flask.test_request.error_handler",
                    "flask.process_response",
                    "flask.do_teardown_request",
                    "flask.do_teardown_appcontext",
                    "flask.response",
                ]
            self.assertListEqual(
                expected_spans,
                [s.name for s in spans],
            )
        else:
            self.assertEqual(len(spans), 10 - REMOVED_SPANS_2_2_0)
            # Assert the order of the spans created
            self.assertListEqual(
                [
                    "flask.request",
                    "flask.application",
                    "flask.try_trigger_before_first_request_functions",
                    "flask.preprocess_request",
                    "flask.dispatch_request",
                    "tests.contrib.flask.test_request.fivehundred",
                    "flask.handle_user_exception",
                    "flask.handle_exception",
                    "tests.contrib.flask.test_request.error_handler",
                    "flask.do_teardown_request",
                    "flask.do_teardown_appcontext",
                    "flask.response",
                ],
                [s.name for s in spans],
            )

        # Assert span services
        for span in spans:
            self.assertEqual(span.service, "flask")

        # Root request span
        req_span = spans[0]
        assert_is_measured(req_span)
        self.assertEqual(req_span.service, "flask")
        self.assertEqual(req_span.name, "flask.request")
        self.assertEqual(req_span.resource, "GET /500")
        self.assertEqual(req_span.span_type, "web")
        self.assertEqual(req_span.error, 1)
        self.assertIsNone(req_span.parent_id)

        # Request tags
        self.assertEqual(req_span.get_tag("http.method"), "GET")
        self.assertEqual(req_span.get_tag(http.URL), "http://localhost/500")
        assert_span_http_status_code(req_span, 500)
        self.assertEqual(req_span.get_tag("flask.endpoint"), "fivehundred")
        self.assertEqual(req_span.get_tag("flask.url_rule"), "/500")

        # Dispatch span
        dispatch_span = spans[4 - REMOVED_SPANS_2_2_0]
        self.assertEqual(dispatch_span.service, "flask")
        self.assertEqual(dispatch_span.name, "flask.dispatch_request")
        self.assertEqual(dispatch_span.resource, "flask.dispatch_request")
        self.assertEqual(dispatch_span.error, 1)
        self.assertTrue(dispatch_span.get_tag(ERROR_MSG).startswith("500 error"))
        self.assertTrue(dispatch_span.get_tag("error.stack").startswith("Traceback"))
        self.assertEqual(dispatch_span.get_tag("error.type"), base_exception_name)

        # Handler span
        handler_span = spans[5 - REMOVED_SPANS_2_2_0]
        self.assertEqual(handler_span.service, "flask")
        self.assertEqual(handler_span.name, "tests.contrib.flask.test_request.fivehundred")
        self.assertEqual(handler_span.resource, "/500")
        self.assertEqual(handler_span.error, 1)
        self.assertTrue(handler_span.get_tag(ERROR_MSG).startswith("500 error"))
        self.assertTrue(handler_span.get_tag("error.stack").startswith("Traceback"))
        self.assertEqual(handler_span.get_tag("error.type"), base_exception_name)

        # User exception span
        user_ex_span = spans[6 - REMOVED_SPANS_2_2_0]
        self.assertEqual(user_ex_span.service, "flask")
        self.assertEqual(user_ex_span.name, "flask.handle_user_exception")
        self.assertEqual(user_ex_span.resource, "flask.handle_user_exception")
        self.assertEqual(user_ex_span.error, 1)
        self.assertTrue(user_ex_span.get_tag(ERROR_MSG).startswith("500 error"))
        self.assertTrue(user_ex_span.get_tag("error.stack").startswith("Traceback"))
        self.assertEqual(user_ex_span.get_tag("error.type"), base_exception_name)

    def test_http_request_header_tracing(self):
        with self.override_http_config("flask", dict(trace_headers=["Host", "my-header"])):
            self.client.get(
                "/",
                headers={
                    "my-header": "my_value",
                },
            )

        traces = self.pop_traces()

        span = traces[0][0]
        assert span.get_tag("http.request.headers.my-header") == "my_value"
        assert span.get_tag("http.request.headers.host") == "localhost"

    @pytest.mark.skipif(flask_version >= (2, 3, 0), reason="Dropped in Flask 2.3.0")
    def test_correct_resource_when_middleware_error(self):
        @self.app.route("/helloworld")
        @self.app.before_first_request
        def error():
            raise Exception()

        self.client.get("/helloworld")

        spans = self.get_spans()
        req_span = spans[0]
        assert req_span.resource == "GET /helloworld"
        assert req_span.get_tag("http.status_code") == "500"

    def test_http_response_header_tracing(self):
        @self.app.route("/response_headers")
        def response_headers():
            resp = make_response("Hello Flask")
            resp.headers["my-response-header"] = "my_response_value"
            return resp

        with self.override_http_config("flask", dict(trace_headers=["my-response-header"])):
            self.client.get("/response_headers")

        traces = self.pop_traces()

        span = traces[0][0]
        assert span.get_tag("http.response.headers.my-response-header") == "my_response_value"

    def test_request_streaming(self):
        def traced_func(i):
            with self.tracer.trace("hello_%d" % (i,)):
                return "Hello From Flask!"

        @self.app.route("/streamed_hello")
        def hello():
            generator = (traced_func(i) for i in range(3))
            return self.app.response_class(generator)

        streamed_resp = self.client.get("/streamed_hello", buffered=True)
        assert streamed_resp.data == b"Hello From Flask!Hello From Flask!Hello From Flask!"
        assert streamed_resp.status_code == 200

        traces = self.pop_traces()
        assert traces[0][0].name == "flask.request"
        assert traces[0][1].name == "flask.application"
        assert traces[0][-4].name == "flask.response"

        # Ensure streamed response are included in the trace
        assert traces[0][-3].name == "hello_0"
        assert traces[0][-2].name == "hello_1"
        assert traces[0][-1].name == "hello_2"


@pytest.mark.parametrize(
    "schema_version, service_name",
    [(None, None), (None, "mysvc"), ("v0", None), ("v0", "mysvc"), ("v1", None), ("v1", "mysvc")],
)
def test_schematized_service_name(ddtrace_run_python_code_in_subprocess, schema_version, service_name):
    """
    v0/Default: expect the service name to be "flask"
    v1: expect the service name to be internal.schema.DEFAULT_SPAN_SERVICE_NAME
    """

    expected_service_name = {
        None: service_name or "flask",
        "v0": service_name or "flask",
        "v1": service_name or DEFAULT_DDTRACE_SUBPROCESS_TEST_SERVICE_NAME,
    }[schema_version]

    code = """
import pytest
from tests.contrib.flask import BaseFlaskTestCase

class TestCase(BaseFlaskTestCase):
    def test(self):
        @self.app.route("/")
        def index():
            return "Hello Flask", 200

        res = self.client.get("/")
        spans = self.get_spans()

        # Root request span
        req_span = spans[0]
        self.assertEqual(req_span.service, "{}")

if __name__ == "__main__":
    import sys
    sys.exit(pytest.main(["-x", __file__]))
    """.format(
        expected_service_name
    )
    env = os.environ.copy()
    if schema_version:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema_version
    if service_name:
        env["DD_SERVICE"] = service_name
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, (out, err)
    assert err == b""


@pytest.mark.parametrize("schema_version", [None, "v0", "v1"])
def test_schematized_operation_name(ddtrace_run_python_code_in_subprocess, schema_version):
    """
    v0/Default: expect the service name to be "flask.request"
    v1: expect the service name to be "http.server.request"
    """

    expected_operation_name = {None: "flask.request", "v0": "flask.request", "v1": "http.server.request"}[
        schema_version
    ]

    code = """
import pytest
from tests.contrib.flask import BaseFlaskTestCase

class TestCase(BaseFlaskTestCase):
    def test(self):
        @self.app.route("/")
        def index():
            return "Hello Flask", 200

        res = self.client.get("/")
        spans = self.pop_spans()

        # Root request span
        req_span = spans[0]
        self.assertEqual(req_span.name, "{}")

if __name__ == "__main__":
    import sys
    sys.exit(pytest.main(["-x", __file__]))
    """.format(
        expected_operation_name
    )
    env = os.environ.copy()
    if schema_version:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema_version
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, (out, err)
    assert err == b"", (out, err)


def test_sigint(tmpdir):
    code = """
from flask import Flask
app = Flask(__name__)

@app.route('/')
def hello_world():
    return 'Hello, World!'

if __name__ == '__main__':
    app.run(port=8082)
    """
    pyfile = tmpdir.join("test.py")
    pyfile.write(code)
    subp = subprocess.Popen(
        ["ddtrace-run", sys.executable, str(pyfile)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=sys.platform != "win32",
    )
    time.sleep(0.5)
    # send two terminate signals to forcibly kill the process
    subp.terminate()
    subp.terminate()
    assert subp.wait() == SIGTERM_EXIT_CODE, "An instrumented Flask app should respond to SIGINT by exiting"
