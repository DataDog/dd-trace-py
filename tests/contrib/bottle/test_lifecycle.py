from collections.abc import Callable
from collections.abc import Iterator
from contextlib import contextmanager

from bottle import Bottle
from bottle import HTTPError
from bottle import request
from webtest import TestApp

from ddtrace.contrib._events.http import HttpRequestBodyAvailability
from ddtrace.contrib._events.http import HttpServerBeforeHandlerEvent
from ddtrace.contrib._events.http import HttpServerBlockResponse
from ddtrace.contrib.internal.bottle.patch import TracePlugin
from ddtrace.internal import core
from tests.utils import TracerTestCase


@contextmanager
def decision_consumer(callback: Callable[[HttpServerBeforeHandlerEvent], None]) -> Iterator[None]:
    core.on(HttpServerBeforeHandlerEvent.event_name, callback)
    try:
        yield
    finally:
        core.reset_listeners(HttpServerBeforeHandlerEvent.event_name, callback)


class TestBottleLifecycle(TracerTestCase):
    def setUp(self):
        super().setUp()
        self.calls = []
        app = Bottle()
        app.install(TracePlugin())

        @app.get("/hello/<name>")
        def hello(name):
            self.calls.append(name)
            return "Hello " + name

        @app.post("/body")
        def body():
            return request.body.read()

        @app.get("/error")
        def error():
            raise HTTPError(418, "teapot")

        self.client = TestApp(app)

    def assert_request_span(self, resource, status):
        spans = self.pop_spans()
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].resource, resource)
        self.assertEqual(spans[0].get_tag("http.status_code"), str(status))
        return spans[0]

    def test_no_consumer(self):
        self.assertEqual(self.client.get("/hello/Ada").text, "Hello Ada")
        self.assertEqual(self.calls, ["Ada"])
        self.assert_request_span("GET /hello/<name>", 200)

    def test_allow_exposes_route_facts_in_active_span(self):
        seen = []

        def inspect(event):
            self.assertIsNotNone(self.tracer.current_span())
            self.assertEqual(self.calls, [])
            seen.append(event)

        with decision_consumer(inspect):
            self.client.get("/hello/Ada?x=1", extra_environ={"SCRIPT_NAME": "/api"})

        self.assertEqual(seen[0].route, "/api/hello/<name>")
        self.assertEqual(seen[0].request_method, "GET")
        self.assertEqual(seen[0].path_parameters, {"name": "Ada"})
        self.assertEqual(seen[0].query, "x=1")
        self.assertIs(seen[0].body_availability, HttpRequestBodyAvailability.NOT_COLLECTED)
        self.assert_request_span("GET /hello/<name>", 200)

    def test_block_skips_handler_and_records_response(self):
        def inspect(event):
            event.block_response = HttpServerBlockResponse(
                status=403, body=b"Denied", headers=(("Content-Type", "text/plain"), ("X-Block-Reason", "demo"))
            )

        with self.override_http_config("bottle", dict(trace_headers=["x-block-reason"])):
            with decision_consumer(inspect):
                response = self.client.get("/hello/Ada", status=403)
        self.assertEqual(response.body, b"Denied")
        self.assertEqual(response.headers["X-Block-Reason"], "demo")
        self.assertEqual(self.calls, [])
        span = self.assert_request_span("GET /hello/<name>", 403)
        self.assertEqual(span.get_tag("http.response.headers.x-block-reason"), "demo")

    def test_decision_does_not_leak_to_next_request(self):
        seen = []

        def inspect(event):
            seen.append(event)
            if event.path_parameters["name"] == "Ada":
                event.block_response = HttpServerBlockResponse()

        with decision_consumer(inspect):
            self.client.get("/hello/Ada", status=403)
            self.assert_request_span("GET /hello/<name>", 403)
            self.client.get("/hello/Grace")

        self.assertEqual(self.calls, ["Grace"])
        self.assertIsNot(seen[0].operation_id, seen[1].operation_id)
        self.assert_request_span("GET /hello/<name>", 200)

    def test_body_is_not_consumed(self):
        with decision_consumer(lambda event: None):
            response = self.client.post("/body", params=b"unconsumed request body", content_type="text/plain")
        self.assertEqual(response.body, b"unconsumed request body")
        self.assert_request_span("POST /body", 200)

    def test_copied_parameters_do_not_modify_handler_arguments(self):
        def inspect(event):
            event.path_parameters["name"] = "changed"

        with decision_consumer(inspect):
            self.assertEqual(self.client.get("/hello/Ada").text, "Hello Ada")

    def test_inspector_failure_is_visible(self):
        def inspect(event):
            raise RuntimeError("inspection failed")

        with decision_consumer(inspect):
            response = self.client.get("/hello/Ada", status=500, expect_errors=True)
        self.assertIn("RuntimeError: inspection failed", response.errors)
        self.assertEqual(self.calls, [])
        self.assert_request_span("GET /hello/<name>", 500)

    def test_framework_exception_preserved(self):
        with decision_consumer(lambda event: None):
            self.client.get("/error", status=418)
        self.assert_request_span("GET /error", 418)

    def test_disabled_instrumentation_skips_event(self):
        seen = []
        self.tracer.enabled = False
        with decision_consumer(seen.append):
            self.client.get("/hello/Ada")
        self.assertEqual(seen, [])
        self.assertEqual(self.calls, ["Ada"])
        self.assertEqual(self.pop_spans(), [])
