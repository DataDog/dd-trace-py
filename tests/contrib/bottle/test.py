from asyncio import CancelledError

import bottle
import webtest

import ddtrace
from ddtrace import config
from ddtrace.constants import USER_KEEP
from ddtrace.contrib.internal.bottle.patch import TracePlugin
from ddtrace.ext import http
from ddtrace.internal import core
from ddtrace.internal.schema.default import DEFAULT_SPAN_SERVICE_NAME
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
            assert self.tracer.current_span().resource == "GET /hi/<name>"
            return "hi %s" % name

        self._trace_app(self.tracer)
        res = self.app.get("/hi/dougie", extra_environ={"SCRIPT_NAME": "/api/v1"})
        assert res.status_code == 200
        spans = self.pop_spans()
        span = spans[0]
        assert span.get_tag("http.route") == "/api/v1/hi/<name>"
        assert span.resource == "GET /hi/<name>"

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


class TestRequestLifecycle(TracerTestCase):
    def setUp(self):
        super().setUp()
        self.app = bottle.Bottle()
        self.app.install(TracePlugin(service=SERVICE))

    def request(self, path="/stream", method="GET", start_response=None):
        environ = webtest.TestRequest.blank(path, method=method).environ
        return self.app(environ, start_response or (lambda status, headers, exc_info=None: None))

    def test_stream_context_and_completion(self):
        spans = []
        contexts = []

        @self.app.route("/stream")
        def stream():
            for chunk in (b"one", b"two"):
                spans.append(self.tracer.current_span())
                contexts.append(core.current)
                yield chunk

        parent_context = core.current
        with self.tracer.trace("parent") as parent:
            body = self.request()
            span = spans[0]  # Bottle peeks at the first chunk before start_response.
            assert not span.finished
            assert span.parent_id == parent.span_id
            assert span.resource == "GET /stream"
            assert self.tracer.current_span() is parent
            assert core.current is parent_context
            assert next(body) == b"one"
            assert next(body) == b"two"
            assert spans == [span, span]
            assert contexts[0] is contexts[1]
            assert self.tracer.current_span() is parent
            assert list(body) == []
            assert span.finished
            body.close()
            body.close()
        assert len(self.pop_spans()) == 2

    def test_close_before_iteration(self):
        spans = []
        closed = []

        @self.app.route("/stream")
        def stream():
            spans.append(self.tracer.current_span())
            try:
                yield b"one"
                yield b"two"
            finally:
                closed.append(True)

        body = self.request()
        assert not spans[0].finished
        body.close()
        body.close()
        assert closed == [True]
        assert spans[0].finished
        assert len(self.pop_spans()) == 1
        assert self.tracer.current_span() is None

    def test_stream_error_after_commit(self):
        @self.app.route("/stream")
        def stream():
            yield b"one"
            raise ValueError("stream failed")

        body = self.request()
        assert next(body) == b"one"
        with self.assertRaisesRegex(ValueError, "stream failed"):
            next(body)
        body.close()
        (span,) = self.pop_spans()
        assert span.error == 1
        assert span.get_tag("error.message") == "stream failed"
        assert_span_http_status_code(span, 200)
        assert self.tracer.current_span() is None

    def test_first_chunk_error(self):
        @self.app.route("/stream")
        def stream():
            raise ValueError("first chunk failed")
            yield b"unreachable"

        body = self.request()
        list(body)
        body.close()
        (span,) = self.pop_spans()
        assert span.error == 1
        assert span.get_tag("error.message") == "first chunk failed"
        assert_span_http_status_code(span, 500)

    def test_route_miss(self):
        body = self.request("/missing")
        list(body)
        body.close()
        (span,) = self.pop_spans()
        assert span.resource == "GET 404"
        assert_span_http_status_code(span, 404)
        assert span.error == 0

    def test_hooks_and_final_response(self):
        active = []

        @self.app.hook("before_request")
        def before():
            active.append(self.tracer.current_span())

        @self.app.hook("after_request")
        def after():
            active.append(self.tracer.current_span())
            bottle.response.status = 202

        @self.app.route("/stream")
        def handler():
            return "done"

        body = self.request()
        list(body)
        body.close()
        (span,) = self.pop_spans()
        assert active == [None, span]
        assert_span_http_status_code(span, 202)

    def test_hook_error(self):
        @self.app.hook("before_request")
        def before():
            raise ValueError("hook failed")

        body = self.request()
        list(body)
        body.close()
        (span,) = self.pop_spans()
        assert span.resource == "GET 500"
        assert span.get_tag("error.message") == "hook failed"
        assert_span_http_status_code(span, 500)

    def test_uninstall_reinstall(self):
        @self.app.route("/stream")
        def handler():
            return "done"

        self.app.uninstall("trace")
        list(self.request())
        assert self.pop_spans() == []
        self.app.install(TracePlugin(service=SERVICE))
        list(self.request())
        assert len(self.pop_spans()) == 1

    def test_head_closes_stream(self):
        spans = []
        closed = []

        @self.app.route("/stream")
        def stream():
            spans.append(self.tracer.current_span())
            try:
                yield b"one"
            finally:
                closed.append(True)

        body = self.request(method="HEAD")
        assert list(body) == []
        body.close()
        assert closed == [True]
        assert spans[0].finished
        assert len(self.pop_spans()) == 1

    def test_close_failure(self):
        @self.app.route("/stream")
        def stream():
            try:
                yield b"one"
                yield b"two"
            finally:
                raise ValueError("close failed")

        body = self.request()
        with self.assertRaisesRegex(ValueError, "close failed"):
            body.close()
        body.close()
        (span,) = self.pop_spans()
        assert span.get_tag("error.message") == "close failed"
        assert_span_http_status_code(span, 200)
        assert self.tracer.current_span() is None

    def test_cancellation(self):
        @self.app.route("/stream")
        def stream():
            yield b"one"
            raise CancelledError()

        body = self.request()
        next(body)
        with self.assertRaises(CancelledError):
            next(body)
        body.close()
        (span,) = self.pop_spans()
        assert span.finished
        assert span.error == 1
        assert self.tracer.current_span() is None

    def test_uncaught_handler_error(self):
        self.app.catchall = False

        @self.app.route("/stream")
        def stream():
            raise ValueError("uncaught")

        with self.assertRaisesRegex(ValueError, "uncaught"):
            self.request()
        (span,) = self.pop_spans()
        assert span.resource == "GET /stream"
        assert span.get_tag("error.message") == "uncaught"
        assert_span_http_status_code(span, 500)
        assert self.tracer.current_span() is None

    def test_interleaved_streams(self):
        active = {}

        @self.app.route("/stream/<name>")
        def stream(name):
            active[name] = [self.tracer.current_span()]
            yield b"one"
            active[name].append(self.tracer.current_span())
            yield b"two"

        first = self.request("/stream/first")
        second = self.request("/stream/second")
        assert active["first"][0] is not active["second"][0]
        assert self.tracer.current_span() is None
        assert list(first) == [b"one", b"two"]
        assert not active["second"][0].finished
        assert list(second) == [b"one", b"two"]
        first.close()
        second.close()
        assert active["first"][0] is active["first"][1]
        assert active["second"][0] is active["second"][1]
        assert len(self.pop_spans()) == 2

    def test_response_headers_and_status(self):
        @self.app.route("/stream")
        def stream():
            return bottle.HTTPResponse("done", status=201, headers={"X-Result": "created"})

        with self.override_http_config("bottle", {"trace_headers": ["x-result"]}):
            received = []
            body = self.request(start_response=lambda status, headers, exc_info: received.append((status, headers)))
            assert list(body) == [b"done"]
            body.close()
        (span,) = self.pop_spans()
        assert_span_http_status_code(span, 201)
        assert received[0][0].startswith("201")
        assert span.get_tag("http.response.headers.x-result") == "created"

    def test_completion_publishes_final_resource_once(self):
        completed = []

        def on_finish(span):
            if span.name == "bottle.request":
                completed.append((span.resource, span.get_tag("http.status_code")))

        @self.app.route("/stream")
        def stream():
            yield b"one"

        core.on("trace.span_finish", on_finish)
        try:
            body = self.request()
            assert completed == []
            assert list(body) == [b"one"]
            body.close()
            body.close()
            assert completed == [("GET /stream", "200")]
        finally:
            core.reset_listeners("trace.span_finish", on_finish)

    def test_start_response_failure(self):
        self.app.catchall = False

        @self.app.route("/stream")
        def stream():
            return b"one"

        def start_response(status, headers, exc_info=None):
            raise OSError("connection closed")

        with self.assertRaisesRegex(OSError, "connection closed"):
            self.request(start_response=start_response)
        (span,) = self.pop_spans()
        assert span.get_tag("error.message") == "connection closed"
        assert self.tracer.current_span() is None

    def test_route_local_plugin(self):
        self.app.uninstall("trace")

        @self.app.route("/stream", apply=[TracePlugin(service="route-service")])
        def stream():
            return b"one"

        body = self.request()
        assert list(body) == [b"one"]
        (span,) = self.pop_spans()
        assert span.resource == "GET /stream"
        assert span.service == "route-service"

    def test_resource_available_to_outer_route_plugin(self):
        seen = []

        class ShortCircuitPlugin:
            name = "short-circuit"
            api = 2

            def apply(self, callback, route):
                def wrapped(*args, **kwargs):
                    seen.append(self_tracer.current_span().resource)
                    return bottle.HTTPResponse("blocked", status=403)

                return wrapped

        self_tracer = self.tracer
        self.app.install(ShortCircuitPlugin())

        @self.app.route("/stream")
        def handler():
            raise AssertionError("handler must not run")

        body = self.request()
        assert list(body) == [b"blocked"]
        body.close()
        (span,) = self.pop_spans()
        assert seen == ["GET /stream"]
        assert span.resource == "GET /stream"
        assert_span_http_status_code(span, 403)

    def test_nested_wsgi_application(self):
        child = bottle.Bottle()
        child.install(TracePlugin(service="child"))

        @child.route("/stream")
        def stream():
            yield b"one"
            yield b"two"

        # A callable forces the WSGI mount path instead of Bottle's route merging.
        # Bottle skips all plugins on a mount proxy by default. Opt in to test nested spans.
        self.app.mount("/child/", lambda environ, start_response: child(environ, start_response), skip=False)
        body = self.request("/child/stream")
        assert list(body) == [b"one", b"two"]
        body.close()
        spans = self.pop_spans()
        assert len(spans) == 2
        parent_span, child_span = sorted(spans, key=lambda span: span.start_ns)
        assert child_span.parent_id == parent_span.span_id
        assert child_span.resource == "GET /stream"
        assert child_span.get_tag("http.route") == "/child/stream"
        assert parent_span.resource != child_span.resource
        assert self.tracer.current_span() is None

    def test_activation_listeners_follow_stream_context(self):
        activated = []

        def on_activate(provider, span):
            if provider is self.tracer.context_provider:
                activated.append(span)

        @self.app.route("/stream")
        def stream():
            for chunk in (b"one", b"two"):
                assert activated[-1] is self.tracer.current_span()
                yield chunk

        with self.tracer.trace("parent") as parent:
            core.on("ddtrace.context_provider.activate", on_activate)
            try:
                body = self.request()
                assert activated[-1] is parent
                assert next(body) == b"one"
                assert activated[-1] is parent
                assert next(body) == b"two"
                assert activated[-1] is parent
                body.close()
                assert activated[-1] is parent
            finally:
                core.reset_listeners("ddtrace.context_provider.activate", on_activate)
