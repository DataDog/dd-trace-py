import molten
from molten.testing import TestClient
import pytest

from ddtrace import Pin
from ddtrace import config
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import ERROR_MSG
from ddtrace.contrib.molten import patch
from ddtrace.contrib.molten import unpatch
from ddtrace.contrib.molten.patch import MOLTEN_VERSION
from ddtrace.ext import http
from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME
from ddtrace.propagation.http import HTTP_HEADER_PARENT_ID
from ddtrace.propagation.http import HTTP_HEADER_TRACE_ID
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured
from tests.utils import assert_span_http_status_code


# NOTE: Type annotations required by molten otherwise parameters cannot be coerced
def hello(name: str, age: int) -> str:
    return f"Hello {age} year old named {name}!"


def greet():
    return "Greetings"


def reply_404():
    raise molten.HTTPError(molten.HTTP_404, {"error": "Nope"})


def molten_app():
    return molten.App(
        routes=[
            molten.Route("/hello/{name}/{age}", hello),
            molten.Route("/greet", greet),
            molten.Route("/404", reply_404),
        ]
    )


class TestMolten(TracerTestCase):
    """Ensures Molten is properly instrumented."""

    TEST_SERVICE = "molten-patch"

    @pytest.mark.usefixtures("molten_app")
    def setUp(self):
        super(TestMolten, self).setUp()
        patch()
        Pin.override(molten, tracer=self.tracer)
        self.app = molten_app()
        self.client = TestClient(self.app)

    def tearDown(self):
        super(TestMolten, self).setUp()
        unpatch()

    def make_request(self, headers=None, params=None):
        uri = self.app.reverse_uri("hello", name="Jim", age=24)
        return self.client.request("GET", uri, headers=headers, params=params)

    def test_route_success(self):
        """Tests request was a success with the expected span tags"""
        response = self.make_request()
        spans = self.pop_spans()
        self.assertEqual(response.status_code, 200)
        # TestResponse from TestClient is wrapper around Response so we must
        # access data property
        self.assertEqual(response.data, '"Hello 24 year old named Jim!"')
        span = spans[0]
        assert_is_measured(span)
        self.assertEqual(span.service, "molten")
        self.assertEqual(span.name, "molten.request")
        self.assertEqual(span.span_type, "web")
        self.assertEqual(span.resource, "GET /hello/{name}/{age}")
        self.assertEqual(span.get_tag("http.method"), "GET")
        self.assertEqual(span.get_tag(http.URL), "http://127.0.0.1:8000/hello/Jim/24")
        self.assertEqual(span.get_tag("component"), "molten")
        self.assertEqual(span.get_tag("span.kind"), "server")
        assert_span_http_status_code(span, 200)
        assert http.QUERY_STRING not in span.get_tags()

        # See test_resources below for specifics of this difference
        if MOLTEN_VERSION >= (0, 7, 2):
            self.assertEqual(len(spans), 18)
        else:
            self.assertEqual(len(spans), 16)

        # test override of service name
        Pin.override(molten, service=self.TEST_SERVICE)
        response = self.make_request()
        spans = self.pop_spans()
        self.assertEqual(spans[0].service, "molten-patch")

    def make_route_reporting_test(self, endpoint, status_code, expected_route):
        self.client.request("GET", endpoint)
        spans = self.pop_spans()
        span = spans[0]
        self.assertEqual(span.service, "molten")
        self.assertEqual(span.name, "molten.request")
        self.assertEqual(span.span_type, "web")
        assert_span_http_status_code(span, status_code)
        self.assertEqual(span.get_tag("http.route"), expected_route)

    def test_route_dynamic(self):
        return self.make_route_reporting_test("/hello/foo/42", 200, "/hello/{name}/{age}")

    def test_route_static(self):
        return self.make_route_reporting_test("/greet", 200, "/greet")

    def test_route_exists_404(self):
        return self.make_route_reporting_test("/404", 404, "/404")

    def test_route_absent_404(self):
        return self.make_route_reporting_test("/not_found", 404, None)

    def test_route_success_query_string(self):
        with self.override_http_config("molten", dict(trace_query_string=True)):
            response = self.make_request(params={"foo": "bar"})
        spans = self.pop_spans()
        self.assertEqual(response.status_code, 200)
        # TestResponse from TestClient is wrapper around Response so we must
        # access data property
        self.assertEqual(response.data, '"Hello 24 year old named Jim!"')
        span = spans[0]
        assert_is_measured(span)
        self.assertEqual(span.service, "molten")
        self.assertEqual(span.name, "molten.request")
        self.assertEqual(span.resource, "GET /hello/{name}/{age}")
        self.assertEqual(span.get_tag("http.method"), "GET")
        self.assertEqual(span.get_tag(http.URL), "http://127.0.0.1:8000/hello/Jim/24?foo=bar")
        self.assertEqual(span.get_tag("component"), "molten")
        self.assertEqual(span.get_tag("span.kind"), "server")
        assert_span_http_status_code(span, 200)
        self.assertEqual(span.get_tag(http.QUERY_STRING), "foo=bar")

    def test_analytics_global_on_integration_default(self):
        """
        When making a request
            When an integration trace search is not event sample rate is not set and globally trace search is enabled
                We expect the root span to have the appropriate tag
        """
        with self.override_global_config(dict(analytics_enabled=True)):
            response = self.make_request()
            self.assertEqual(response.status_code, 200)
            # TestResponse from TestClient is wrapper around Response so we must
            # access data property
            self.assertEqual(response.data, '"Hello 24 year old named Jim!"')

            root_span = self.get_root_span()
            root_span.assert_matches(
                name="molten.request",
                metrics={ANALYTICS_SAMPLE_RATE_KEY: 1.0},
            )

    def test_analytics_global_on_integration_on(self):
        """
        When making a request
            When an integration trace search is enabled and sample rate is set and globally trace search is enabled
                We expect the root span to have the appropriate tag
        """
        with self.override_global_config(dict(analytics_enabled=True)):
            with self.override_config("molten", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
                response = self.make_request()
                self.assertEqual(response.status_code, 200)
                # TestResponse from TestClient is wrapper around Response so we must
                # access data property
                self.assertEqual(response.data, '"Hello 24 year old named Jim!"')

                root_span = self.get_root_span()
                root_span.assert_matches(
                    name="molten.request",
                    metrics={ANALYTICS_SAMPLE_RATE_KEY: 0.5},
                )

    def test_analytics_global_off_integration_default(self):
        """
        When making a request
            When an integration trace search is not set and sample rate is set and globally trace search is disabled
                We expect the root span to not include tag
        """
        with self.override_global_config(dict(analytics_enabled=False)):
            response = self.make_request()
            self.assertEqual(response.status_code, 200)
            # TestResponse from TestClient is wrapper around Response so we must
            # access data property
            self.assertEqual(response.data, '"Hello 24 year old named Jim!"')

            root_span = self.get_root_span()
            self.assertIsNone(root_span.get_metric(ANALYTICS_SAMPLE_RATE_KEY))

    def test_analytics_global_off_integration_on(self):
        """
        When making a request
            When an integration trace search is enabled and sample rate is set and globally trace search is disabled
                We expect the root span to have the appropriate tag
        """
        with self.override_global_config(dict(analytics_enabled=False)):
            with self.override_config("molten", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
                response = self.make_request()
                self.assertEqual(response.status_code, 200)
                # TestResponse from TestClient is wrapper around Response so we must
                # access data property
                self.assertEqual(response.data, '"Hello 24 year old named Jim!"')

                root_span = self.get_root_span()
                root_span.assert_matches(
                    name="molten.request",
                    metrics={ANALYTICS_SAMPLE_RATE_KEY: 0.5},
                )

    def test_route_failure(self):
        app = molten.App(routes=[molten.Route("/hello/{name}/{age}", hello)])
        client = TestClient(app)
        response = client.get("/goodbye")
        spans = self.pop_spans()
        self.assertEqual(response.status_code, 404)
        span = spans[0]
        assert_is_measured(span)
        self.assertEqual(span.service, "molten")
        self.assertEqual(span.name, "molten.request")
        self.assertEqual(span.resource, "GET 404")
        self.assertEqual(span.get_tag(http.URL), "http://127.0.0.1:8000/goodbye")
        self.assertEqual(span.get_tag("http.method"), "GET")
        self.assertEqual(span.get_tag("component"), "molten")
        self.assertEqual(span.get_tag("span.kind"), "server")
        assert_span_http_status_code(span, 404)

    def test_route_exception(self):
        def route_error() -> str:
            raise Exception("Error message")

        app = molten.App(routes=[molten.Route("/error", route_error)])
        client = TestClient(app)
        response = client.get("/error")
        spans = self.pop_spans()
        self.assertEqual(response.status_code, 500)
        span = spans[0]
        assert_is_measured(span)
        route_error_span = spans[-1]
        self.assertEqual(span.service, "molten")
        self.assertEqual(span.name, "molten.request")
        self.assertEqual(span.resource, "GET /error")
        self.assertEqual(span.error, 1)
        # error tags only set for route function span and not root span
        self.assertIsNone(span.get_tag(ERROR_MSG))
        self.assertEqual(route_error_span.get_tag(ERROR_MSG), "Error message")
        self.assertEqual(span.get_tag("component"), "molten")
        self.assertEqual(span.get_tag("span.kind"), "server")

    def test_resources(self):
        """Tests request has expected span resources"""
        self.make_request()
        spans = self.pop_spans()

        # `can_handle_parameter` appears twice since two parameters are in request
        # TODO[tahir]: missing ``resolve` method for components

        expected = [
            "GET /hello/{name}/{age}",
            "molten.middleware.ResponseRendererMiddleware",
            "molten.components.HeaderComponent.can_handle_parameter",
            "molten.components.CookiesComponent.can_handle_parameter",
            "molten.components.QueryParamComponent.can_handle_parameter",
            "molten.components.RequestBodyComponent.can_handle_parameter",
            "molten.components.RequestDataComponent.can_handle_parameter",
            "molten.components.SchemaComponent.can_handle_parameter",
            "molten.components.UploadedFileComponent.can_handle_parameter",
            "molten.components.HeaderComponent.can_handle_parameter",
            "molten.components.CookiesComponent.can_handle_parameter",
            "molten.components.QueryParamComponent.can_handle_parameter",
            "molten.components.RequestBodyComponent.can_handle_parameter",
            "molten.components.RequestDataComponent.can_handle_parameter",
            "molten.components.SchemaComponent.can_handle_parameter",
            "molten.components.UploadedFileComponent.can_handle_parameter",
            "tests.contrib.molten.test_molten.hello",
            "molten.renderers.JSONRenderer.render",
        ]

        # Addition of `UploadedFileComponent` in 0.7.2 changes expected spans
        if MOLTEN_VERSION < (0, 7, 2):
            expected = [r for r in expected if not r.startswith("molten.components.UploadedFileComponent")]

        self.assertEqual(len(spans), len(expected))
        self.assertEqual([s.resource for s in spans], expected)

    def test_distributed_tracing(self):
        """Tests whether span IDs are propagated when distributed tracing is on"""
        # Default: distributed tracing enabled
        response = self.make_request(
            headers={
                HTTP_HEADER_TRACE_ID: "100",
                HTTP_HEADER_PARENT_ID: "42",
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), "Hello 24 year old named Jim!")

        spans = self.pop_spans()
        span = spans[0]
        self.assertEqual(span.name, "molten.request")
        self.assertEqual(span.trace_id, 100)
        self.assertEqual(span.parent_id, 42)

        # Explicitly enable distributed tracing
        with self.override_config("molten", dict(distributed_tracing=True)):
            response = self.make_request(
                headers={
                    HTTP_HEADER_TRACE_ID: "100",
                    HTTP_HEADER_PARENT_ID: "42",
                }
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), "Hello 24 year old named Jim!")

        spans = self.pop_spans()
        span = spans[0]
        self.assertEqual(span.name, "molten.request")
        self.assertEqual(span.trace_id, 100)
        self.assertEqual(span.parent_id, 42)

        # Now without tracing on
        with self.override_config("molten", dict(distributed_tracing=False)):
            response = self.make_request(
                headers={
                    HTTP_HEADER_TRACE_ID: "100",
                    HTTP_HEADER_PARENT_ID: "42",
                }
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), "Hello 24 year old named Jim!")

        spans = self.pop_spans()
        span = spans[0]
        self.assertEqual(span.name, "molten.request")
        self.assertNotEqual(span.trace_id, 100)
        self.assertNotEqual(span.parent_id, 42)

    def test_unpatch_patch(self):
        """Tests unpatch-patch cycle"""
        unpatch()
        self.assertIsNone(Pin.get_from(molten))
        self.make_request()
        spans = self.pop_spans()
        self.assertEqual(len(spans), 0)

        patch()
        # Need to override Pin here as we do in setUp
        Pin.override(molten, tracer=self.tracer)
        self.assertTrue(Pin.get_from(molten) is not None)
        self.make_request()
        spans = self.pop_spans()
        self.assertTrue(len(spans) > 0)

    def test_patch_unpatch(self):
        """Tests repatch-unpatch cycle"""
        # Already call patch in setUp
        self.assertTrue(Pin.get_from(molten) is not None)
        self.make_request()
        spans = self.pop_spans()
        self.assertTrue(len(spans) > 0)

        # Test unpatch
        unpatch()
        self.assertTrue(Pin.get_from(molten) is None)
        self.make_request()
        spans = self.pop_spans()
        self.assertEqual(len(spans), 0)

    def test_patch_idempotence(self):
        """Tests repatching"""
        # Already call patch in setUp but patch again
        patch()
        self.make_request()
        spans = self.pop_spans()
        self.assertTrue(len(spans) > 0)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_user_specified_service_default_schema(self):
        """
        default: When a service name is specified by the user
            The molten integration should use it as the service name
        """
        self.make_request()
        spans = self.pop_spans()
        for span in spans:
            assert span.service == "mysvc", "Expected 'mysvc' but got {}".format(span.service)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_user_specified_service_v0_schema(self):
        """
        v0: When a service name is specified by the user
            The molten integration should use it as the service name
        """
        self.make_request()
        spans = self.pop_spans()
        for span in spans:
            assert span.service == "mysvc", "Expected 'mysvc' but got {}".format(span.service)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_user_specified_service_v1_schema(self):
        """
        v1: When a service name is specified by the user
            The molten integration should use it as the service name
        """
        self.make_request()
        spans = self.pop_spans()
        for span in spans:
            assert span.service == "mysvc", "Expected 'mysvc' but got {}".format(span.service)

    @TracerTestCase.run_in_subprocess()
    def test_unspecified_service_default_schema(self):
        """
        default: When a service name is not specified by the user
            The molten integration should use 'molten' as the service name
        """
        self.make_request()
        spans = self.pop_spans()
        for span in spans:
            assert span.service == "molten", "Expected 'molten' but got {}".format(span.service)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_unspecified_service_v0_schema(self):
        """
        v0: When a service name is not specified by the user
            The molten integration should use 'molten' as the service name
        """
        self.make_request()
        spans = self.pop_spans()
        for span in spans:
            assert span.service == "molten", "Expected 'molten' but got {}".format(span.service)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_unspecified_service_v1_schema(self):
        """
        v1: When a service name is not specified by the user
            The molten integration should use internal.schema.DEFAULT_SPAN_SERVICE_NAME as the service name
        """
        self.make_request()
        spans = self.pop_spans()
        for span in spans:
            assert span.service == DEFAULT_SPAN_SERVICE_NAME, "Expected '{}' but got {}".format(
                DEFAULT_SPAN_SERVICE_NAME, span.service
            )

    @TracerTestCase.run_in_subprocess()
    def test_schematized_operation_name_default(self):
        """
        v0: The molten span name should be "request.span"
        """
        self.make_request()
        spans = self.pop_spans()
        root_span = spans[0]
        assert root_span.name == "molten.request", "Expected 'molten.request' but got {}".format(root_span.name)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_operation_name_v0(self):
        """
        v0: The molten span name should be "request.span"
        """
        self.make_request()
        spans = self.pop_spans()
        root_span = spans[0]
        assert root_span.name == "molten.request", "Expected 'molten.request' but got {}".format(root_span.name)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_operation_name_v1(self):
        """
        v1: The molten span name should be "http.server.request"
        """
        self.make_request()
        spans = self.pop_spans()
        root_span = spans[0]
        assert root_span.name == "http.server.request", "Expected 'http.server.request' but got {}".format(
            root_span.name
        )

    def test_http_request_header_tracing(self):
        config.molten.http.trace_headers(["my-header"])
        response = self.make_request(
            headers={
                "my-header": "my_value",
            }
        )
        self.assertEqual(response.status_code, 200)

        spans = self.pop_spans()
        span = spans[0]
        self.assertEqual(span.name, "molten.request")
        self.assertEqual(span.get_tag("http.request.headers.my-header"), "my_value")
