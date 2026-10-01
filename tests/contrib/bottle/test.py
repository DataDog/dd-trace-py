import bottle
import pytest
import webtest

import ddtrace
from ddtrace import config
from ddtrace.constants import USER_KEEP
from ddtrace.contrib.internal.bottle.patch import TracePlugin
from ddtrace.ext import http
from ddtrace.internal.schema.default import DEFAULT_SPAN_SERVICE_NAME
from tests.contrib.otel_http_server import OTEL_SERVER_ENV
from tests.contrib.otel_http_server import OTEL_SERVER_ERROR_STATUSES_ENV
from tests.tracer.utils_inferred_spans.test_helpers import assert_web_and_inferred_aws_api_gateway_span_data
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured
from tests.utils import assert_span_http_status_code


SERVICE = "bottle-app"


class TraceBottleTest(TracerTestCase):
    """
    Ensures that Bottle is properly traced.
    """

    def setUp(self):
        super().setUp()
        # provide a Bottle app
        self.app = bottle.Bottle()

    def tearDown(self):
        super().tearDown()

    def _trace_app(self, tracer=None, extra_environ={}):
        self.app.install(TracePlugin(service=SERVICE, tracer=tracer))
        self.app = webtest.TestApp(self.app, extra_environ=extra_environ)

    def test_200(self, query_string=""):
        if query_string:
            fqs = "?" + query_string
        else:
            fqs = ""

        # setup our test app
        @self.app.route("/hi/<name>")
        def hi(name):
            return "hi %s" % name

        self._trace_app(self.tracer)

        # make a request
        resp = self.app.get("/hi/dougie" + fqs)
        assert resp.status_int == 200
        assert resp.body.decode("utf-8", errors="ignore") == "hi dougie"
        # validate it's traced
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]

        assert_is_measured(s)
        assert s.name == "bottle.request"
        assert s.service == "bottle-app"
        assert s.span_type == "web"
        assert s.resource == "GET /hi/<name>"
        assert_span_http_status_code(s, 200)
        assert s.get_tag("http.method") == "GET"
        assert s.get_tag("component") == "bottle"
        assert s.get_tag("span.kind") == "server"
        assert s.get_tag("http.route") == "/hi/<name>"
        if ddtrace.config.bottle.trace_query_string:
            assert s.get_tag(http.QUERY_STRING) == query_string
        else:
            assert http.QUERY_STRING not in s.get_tags()

        if ddtrace.config.bottle.http_tag_query_string:
            assert s.get_tag(http.URL) == "http://localhost:80/hi/dougie" + fqs
        else:
            assert s.get_tag(http.URL) == "http://localhost:80/hi/dougie"

    def test_app_root(self):
        @self.app.route("/hi/<name>")
        def hi(name):
            return "hi %s" % name

        self._trace_app(self.tracer)
        res = self.app.get("/hi/dougie", extra_environ={"SCRIPT_NAME": "/api/v1"})
        assert res.status_code == 200
        spans = self.pop_spans()
        span = spans[0]
        assert span.get_tag("http.route") == "/api/v1/hi/<name>"

    def test_query_string(self):
        return self.test_200("foo=bar")

    def test_query_string_multi_keys(self):
        return self.test_200("foo=bar&foo=baz&x=y")

    def test_query_string_trace(self):
        with self.override_http_config("bottle", dict(trace_query_string=True)):
            return self.test_200("foo=bar")

    def test_disabled_http_tag_query_string(self):
        with self.override_config("bottle", dict(http_tag_query_string=False)):
            return self.test_200("foo=bar")

    def test_query_string_multi_keys_trace(self):
        with self.override_http_config("bottle", dict(trace_query_string=True)):
            return self.test_200("foo=bar&foo=baz&x=y")

    def test_2xx(self):
        @self.app.route("/2xx")
        def handled():
            return bottle.HTTPResponse("", status=202)

        self._trace_app(self.tracer)

        # make a request
        try:
            self.app.get("/2xx")
        except webtest.AppError:
            pass

        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.resource == "GET /2xx"
        assert_span_http_status_code(s, 202)
        assert s.error == 0

    def test_400_return(self):
        @self.app.route("/400_return")
        def handled400():
            return bottle.HTTPResponse(status=400)

        self._trace_app(self.tracer)

        # make a request
        try:
            self.app.get("/400_return")
        except webtest.AppError:
            pass

        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]

        assert_is_measured(s)
        assert s.name == "bottle.request"
        assert s.service == "bottle-app"
        assert s.resource == "GET /400_return"
        assert_span_http_status_code(s, 400)
        assert s.get_tag("http.method") == "GET"
        assert s.get_tag(http.URL) == "http://localhost:80/400_return"
        assert s.get_tag("component") == "bottle"
        assert s.get_tag("span.kind") == "server"
        assert s.get_tag("http.route") == "/400_return"
        assert s.error == 0

    def test_400_raise(self):
        @self.app.route("/400_raise")
        def handled400():
            raise bottle.HTTPResponse(status=400)

        self._trace_app(self.tracer)

        # make a request
        try:
            self.app.get("/400_raise")
        except webtest.AppError:
            pass

        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]

        assert_is_measured(s)
        assert s.name == "bottle.request"
        assert s.service == "bottle-app"
        assert s.resource == "GET /400_raise"
        assert_span_http_status_code(s, 400)
        assert s.get_tag("http.method") == "GET"
        assert s.get_tag(http.URL) == "http://localhost:80/400_raise"
        assert s.get_tag("component") == "bottle"
        assert s.get_tag("span.kind") == "server"
        assert s.get_tag("http.route") == "/400_raise"
        assert s.error == 1

    def test_500(self):
        @self.app.route("/hi")
        def hi():
            raise Exception("oh no")

        self._trace_app(self.tracer)

        # make a request
        try:
            self.app.get("/hi")
        except webtest.AppError:
            pass

        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]

        assert_is_measured(s)
        assert s.name == "bottle.request"
        assert s.service == "bottle-app"
        assert s.resource == "GET /hi"
        assert_span_http_status_code(s, 500)
        assert s.get_tag("http.method") == "GET"
        assert s.get_tag(http.URL) == "http://localhost:80/hi"
        assert s.get_tag("component") == "bottle"
        assert s.get_tag("span.kind") == "server"
        assert s.get_tag("http.route") == "/hi"
        assert s.error == 1

    def test_5XX_response(self):
        """
        When a 5XX response is returned
            The span error attribute should be 1
        """

        @self.app.route("/5XX-1")
        def handled500_1():
            raise bottle.HTTPResponse(status=503)

        @self.app.route("/5XX-2")
        def handled500_2():
            raise bottle.HTTPError(status=502)

        @self.app.route("/5XX-3")
        def handled500_3():
            bottle.response.status = 503
            return "hmmm"

        self._trace_app(self.tracer)

        try:
            self.app.get("/5XX-1")
        except webtest.AppError:
            pass
        spans = self.pop_spans()
        assert len(spans) == 1
        assert spans[0].error == 1

        try:
            self.app.get("/5XX-2")
        except webtest.AppError:
            pass
        spans = self.pop_spans()
        assert len(spans) == 1
        assert spans[0].error == 1

        try:
            self.app.get("/5XX-3")
        except webtest.AppError:
            pass
        spans = self.pop_spans()
        assert len(spans) == 1
        assert spans[0].error == 1

    def test_abort(self):
        @self.app.route("/hi")
        def hi():
            raise bottle.abort(420, "Enhance Your Calm")

        self._trace_app(self.tracer)

        # make a request
        try:
            self.app.get("/hi")
        except webtest.AppError:
            pass

        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]

        assert_is_measured(s)
        assert s.name == "bottle.request"
        assert s.service == "bottle-app"
        assert s.resource == "GET /hi"
        assert_span_http_status_code(s, 420)
        assert s.get_tag("http.method") == "GET"
        assert s.get_tag(http.URL) == "http://localhost:80/hi"
        assert s.get_tag("component") == "bottle"
        assert s.get_tag("span.kind") == "server"
        assert s.get_tag("http.route") == "/hi"

    def test_bottle_global_tracer(self):
        # without providing a Tracer instance, it should work
        @self.app.route("/home/")
        def home():
            return "Hello world"

        self._trace_app()

        # make a request
        resp = self.app.get("/home/")
        assert resp.status_int == 200
        # validate it's traced
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.name == "bottle.request"
        assert s.service == "bottle-app"
        assert s.resource == "GET /home/"
        assert_span_http_status_code(s, 200)
        assert s.get_tag("http.method") == "GET"
        assert s.get_tag(http.URL) == "http://localhost:80/home/"
        assert s.get_tag("component") == "bottle"
        assert s.get_tag("span.kind") == "server"
        assert s.get_tag("http.route") == "/home/"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_user_specified_service_default_schema(self):
        """
        default/v0: When a service name is specified by the user
            The bottle integration should use it as the service name
        """

        @self.app.route("/hi/<name>")
        def hi(name):
            return "hi %s" % name

        self._trace_app(self.tracer)
        resp = self.app.get("/hi/dougie")
        assert resp.status_int == 200
        root = self.get_root_span()
        root.assert_matches(service="mysvc")

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_user_specified_service_v0_schema(self):
        """
        v0: When a service name is specified by the user
            The bottle integration should use it as the service name
        """

        @self.app.route("/hi/<name>")
        def hi(name):
            return "hi %s" % name

        self._trace_app(self.tracer)
        resp = self.app.get("/hi/dougie")
        assert resp.status_int == 200
        root = self.get_root_span()
        root.assert_matches(service="mysvc")

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_user_specified_service_v1_schema(self):
        """
        v1: When a service name is specified by the user
            The bottle integration should use it as the service name
        """

        @self.app.route("/hi/<name>")
        def hi(name):
            return "hi %s" % name

        self._trace_app(self.tracer)
        resp = self.app.get("/hi/dougie")
        assert resp.status_int == 200
        root = self.get_root_span()
        root.assert_matches(service="mysvc")

    @TracerTestCase.run_in_subprocess()
    def test_unspecified_service_default_schema(self):
        """
        default/v0: When a service name is not specified by the user
            The bottle integration should use the applications name as the service name (or "bottle")
        """

        @self.app.route("/hi/<name>")
        def hi(name):
            return "hi %s" % name

        self.app.install(TracePlugin(tracer=self.tracer))
        self.app = webtest.TestApp(self.app)
        resp = self.app.get("/hi/dougie")
        assert resp.status_int == 200
        root = self.get_root_span()
        root.assert_matches(service="bottle")

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_unspecified_service_v0_schema(self):
        """
        default/v0: When a service name is not specified by the user
            The bottle integration should use "bottle" as the service name
        """

        @self.app.route("/hi/<name>")
        def hi(name):
            return "hi %s" % name

        self.app.install(TracePlugin(tracer=self.tracer))
        self.app = webtest.TestApp(self.app)
        resp = self.app.get("/hi/dougie")
        assert resp.status_int == 200
        root = self.get_root_span()
        root.assert_matches(service="bottle")

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_unspecified_service_v1_schema(self):
        """
        v1: When a service name is not specified by the user
            The bottle integration should use internal.schema.DEFAULT_SERVICE_SPAN as the service name
        """

        @self.app.route("/hi/<name>")
        def hi(name):
            return "hi %s" % name

        self._trace_app(self.tracer)
        resp = self.app.get("/hi/dougie")
        assert resp.status_int == 200
        root = self.get_root_span()
        root.assert_matches(service=DEFAULT_SPAN_SERVICE_NAME)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_operation_name_v0_schema(self):
        """
        v0: When a service name is not specified by the user
            Then we expect 'bottle.request'
        """

        @self.app.route("/hi/<name>")
        def hi(name):
            return "hi %s" % name

        self._trace_app(self.tracer)
        resp = self.app.get("/hi/dougie")
        assert resp.status_int == 200
        root = self.get_root_span()
        root.assert_matches(name="bottle.request")

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_operation_name_v1_schema(self):
        """
        v1: When a service name is not specified by the user
            Then we expect 'http.server.request'
        """

        @self.app.route("/hi/<name>")
        def hi(name):
            return "hi %s" % name

        self._trace_app(self.tracer)
        resp = self.app.get("/hi/dougie")
        assert resp.status_int == 200
        root = self.get_root_span()
        import os

        assert "DD_TRACE_SPAN_ATTRIBUTE_SCHEMA" in os.environ
        assert os.environ["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] == "v1"
        root.assert_matches(name="http.server.request")

    def test_http_request_header_tracing(self):
        config.bottle.http.trace_headers(["my-header"])

        # setup our test app
        @self.app.route("/home/")
        def home():
            return "Hello world"

        self._trace_app()

        # make a request
        resp = self.app.get(
            "/home/",
            headers={
                "my-header": "my_value",
            },
        )
        assert resp.status_int == 200
        # validate it's traced
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]

        assert s.get_tag("http.request.headers.my-header") == "my_value"

    def test_http_response_header_tracing(self):
        config.bottle.http.trace_headers(["my-response-header"])

        # setup our test app
        @self.app.route("/home/")
        def home():
            bottle.response.headers["my-response-header"] = "my_response_value"
            return "Hello world"

        self._trace_app()

        # make a request
        resp = self.app.get(
            "/home/",
        )
        assert resp.status_int == 200
        # validate it's traced
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]

        assert s.get_tag("http.response.headers.my-response-header") == "my_response_value"

    def test_inferred_spans_api_gateway(self):
        @self.app.route("/")
        def default_endpoint():
            return bottle.HTTPResponse("", status=200)

        @self.app.route("/exception")
        def error_endpoint():
            raise Exception("oh no")

        @self.app.route("/handled")
        def handled_error_endpoint():
            return bottle.HTTPResponse("", status=503)

        self._trace_app()

        test_headers = {
            "x-dd-proxy": "aws-apigateway",
            "x-dd-proxy-request-time-ms": "1736973768000",
            "x-dd-proxy-path": "/",
            "x-dd-proxy-httpmethod": "GET",
            "x-dd-proxy-domain-name": "local",
            "x-dd-proxy-stage": "stage",
        }

        for setting_enabled in [False, True]:
            ddtrace.config._inferred_proxy_services_enabled = setting_enabled
            for test_endpoint in [
                {"endpoint": "/", "status": 200, "url": "http://localhost:80/"},
                {"endpoint": "/exception", "status": 500, "url": "http://localhost:80/exception"},
                {"endpoint": "/handled", "status": 503, "url": "http://localhost:80/handled"},
            ]:
                try:
                    self.app.get(test_endpoint["endpoint"], headers=test_headers)
                except webtest.AppError:
                    pass

                traces = self.pop_traces()
                aws_gateway_span = traces[0][0]

                if setting_enabled:
                    web_span = traces[0][1]

                    assert_web_and_inferred_aws_api_gateway_span_data(
                        aws_gateway_span,
                        web_span,
                        web_span_name="bottle.request",
                        web_span_component="bottle",
                        web_span_service_name=SERVICE,
                        web_span_resource="GET " + test_endpoint["endpoint"],
                        api_gateway_service_name="local",
                        api_gateway_resource="GET /",
                        method="GET",
                        status_code=str(test_endpoint["status"]),
                        url="https://local/",
                        start=1736973768,
                        is_distributed=False,
                        distributed_trace_id=1,
                        distributed_parent_id=2,
                        distributed_sampling_priority=USER_KEEP,
                    )

                else:
                    web_span = traces[0][0]
                    assert web_span.name == "bottle.request"
                    assert web_span._parent is None


@pytest.mark.subprocess(env=OTEL_SERVER_ENV, ddtrace_run=True)
def test_otel_semantics_server_span_attributes():
    from functools import partial

    import bottle
    import webtest

    from tests.contrib.otel_http_server import TEST_HEADERS
    from tests.contrib.otel_http_server import assert_otel_server_span
    from tests.utils import TracerSpanContainer
    from tests.utils import scoped_tracer

    assert_span = partial(assert_otel_server_span, peer_address=False)

    app = bottle.Bottle()

    @app.route("/hi/<name>")
    def hi(name):
        return "hi %s" % name

    @app.route("/hi/<name>", method="PROPFIND")
    def hi_propfind(name):
        return "hi %s" % name

    @app.route("/gone")
    def gone():
        bottle.response.status = 410
        return "gone"

    @app.route("/missing/<name>")
    def missing(name):
        bottle.response.status = 404
        return "missing"

    @app.route("/broken")
    def broken():
        bottle.response.status = 500
        return "oops"

    with scoped_tracer() as tracer:
        spans = TracerSpanContainer(tracer)
        client = webtest.TestApp(app, lint=False)

        def request_span(path, method="GET"):
            spans.reset()
            response = client.request(path, method=method, headers=TEST_HEADERS, expect_errors=True)
            return response, spans.get_root_span()

        response, span = request_span("/hi/dougie?q=1")
        assert response.status_int == 200
        assert_span(
            span,
            method="GET",
            status=200,
            path="/hi/dougie",
            query="q=1",
            route="/hi/<name>",
            resource="GET /hi/<name>",
        )

        response, span = request_span("/hi/dougie", method="PROPFIND")
        assert response.status_int == 200
        assert_span(
            span,
            method="_OTHER",
            original_method="PROPFIND",
            status=200,
            path="/hi/dougie",
            route="/hi/<name>",
            resource="HTTP /hi/<name>",
        )

        # A 404 raised by a matched route reports the route template, never the URL path.
        response, span = request_span("/missing/secret-123")
        assert response.status_int == 404
        assert_span(
            span,
            method="GET",
            status=404,
            path="/missing/secret-123",
            route="/missing/<name>",
            resource="GET /missing/<name>",
        )

        response, span = request_span("/gone")
        assert response.status_int == 410
        assert_span(span, method="GET", status=410, path="/gone", route="/gone", resource="GET /gone")

        response, span = request_span("/broken")
        assert response.status_int == 500
        assert_span(span, method="GET", status=500, path="/broken", route="/broken", resource="GET /broken")


@pytest.mark.subprocess(env=OTEL_SERVER_ERROR_STATUSES_ENV, ddtrace_run=True)
def test_otel_semantics_server_error_statuses_override():
    import bottle
    import webtest

    from tests.contrib.otel_http_server import TEST_HEADERS
    from tests.utils import TracerSpanContainer
    from tests.utils import scoped_tracer

    app = bottle.Bottle()

    @app.route("/missing")
    def missing():
        bottle.response.status = 404
        return "missing"

    @app.route("/broken")
    def broken():
        bottle.response.status = 500
        return "oops"

    with scoped_tracer() as tracer:
        spans = TracerSpanContainer(tracer)
        client = webtest.TestApp(app, lint=False)

        assert client.get("/missing", headers=TEST_HEADERS, expect_errors=True).status_int == 404
        span = spans.get_root_span()
        assert span.error == 1
        assert span.get_tag("error.type") == "404"
        assert span.resource == "GET /missing"
        assert span.get_metric("http.response.status_code") == 404

        spans.reset()
        assert client.get("/broken", headers=TEST_HEADERS, expect_errors=True).status_int == 500
        span = spans.get_root_span()
        assert span.error == 0
        assert span.get_tag("error.type") is None
        assert span.resource == "GET /broken"
        assert span.get_metric("http.response.status_code") == 500


# Product bug: the plugin re-raises the HTTPError from abort() inside context_with_event
# (ddtrace/contrib/internal/bottle/trace.py:76-86), so the exception marks a 404 span as an error
# even though only 5xx statuses are errors under OTel semantics.
@pytest.mark.subprocess(env=OTEL_SERVER_ENV, ddtrace_run=True)
def test_otel_semantics_server_abort_4xx_is_not_an_error():
    import bottle
    import webtest

    from tests.utils import TracerSpanContainer
    from tests.utils import scoped_tracer

    app = bottle.Bottle()

    @app.route("/missing")
    def missing():
        bottle.abort(404)

    with scoped_tracer() as tracer:
        spans = TracerSpanContainer(tracer)
        webtest.TestApp(app, lint=False).get("/missing", expect_errors=True)
        span = spans.get_root_span()
        assert span.get_metric("http.response.status_code") == 404
        assert span.error == 0
        assert span.get_tag("error.type") is None
