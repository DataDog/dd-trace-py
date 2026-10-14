from bottle import Bottle
from bottle import HTTPError
from webtest import TestApp

from tests.utils import TracerTestCase

from .adapter import ScaffoldTracePlugin
from .asm_consumer import AsmConsumer
from .asm_consumer import RequestFacts
from .contracts import BlockResponse
from .contracts import BodyAvailability


class TestOwnershipScaffold(TracerTestCase):
    def setUp(self) -> None:
        super().setUp()  # type: ignore[no-untyped-call]
        self.calls: list[str] = []
        app = Bottle()
        app.install(ScaffoldTracePlugin())  # type: ignore[no-untyped-call]

        @app.get("/hello/<name>")  # type: ignore[misc]
        def hello(name: str) -> str:
            self.calls.append(name)
            return "Hello " + name

        @app.get("/error")  # type: ignore[misc]
        def error() -> None:
            raise HTTPError(418, "teapot")

        self.client = TestApp(app)

    def assert_request_span(self, resource: str, status: int) -> None:
        spans = self.pop_spans()
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].resource, resource)
        self.assertEqual(spans[0].get_tag("http.status_code"), str(status))

    def test_no_consumer(self) -> None:
        self.assertEqual(self.client.get("/hello/Ada").text, "Hello Ada")
        self.assertEqual(self.calls, ["Ada"])
        self.assert_request_span("GET /hello/<name>", 200)

    def test_allow_exposes_facts(self) -> None:
        seen: list[RequestFacts] = []
        with AsmConsumer(lambda facts: seen.append(facts)):
            self.client.get("/hello/Ada?x=1")
        self.assertEqual(seen[0].route, "/hello/<name>")
        self.assertEqual(seen[0].path_parameters, (("name", "Ada"),))
        self.assertEqual(seen[0].query, "x=1")
        self.assertIs(seen[0].body_availability, BodyAvailability.NOT_COLLECTED)
        self.assert_request_span("GET /hello/<name>", 200)

    def test_block_skips_handler_and_cleanup_allows_next_request(self) -> None:
        with AsmConsumer(lambda facts: BlockResponse()):
            self.assertEqual(self.client.get("/hello/Ada", status=403).body, b"Blocked")
        self.assertEqual(self.calls, [])
        self.assert_request_span("GET /hello/<name>", 403)
        self.client.get("/hello/Grace")
        self.assertEqual(self.calls, ["Grace"])
        self.assert_request_span("GET /hello/<name>", 200)

    def test_inspector_failure_is_visible(self) -> None:
        def inspect(facts: RequestFacts) -> None:
            raise RuntimeError("inspection failed")

        with AsmConsumer(inspect):
            response = self.client.get("/hello/Ada", status=500, expect_errors=True)
            self.assertIn("RuntimeError: inspection failed", response.errors)
        self.assertEqual(self.calls, [])
        self.assert_request_span("GET /hello/<name>", 500)

    def test_framework_exception_preserved(self) -> None:
        with AsmConsumer(lambda facts: None):
            self.client.get("/error", status=418)
        self.assert_request_span("GET /error", 418)
