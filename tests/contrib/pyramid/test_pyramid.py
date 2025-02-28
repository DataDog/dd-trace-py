import os
import shutil
import subprocess
from tempfile import gettempdir

import pytest

from ddtrace import config
from ddtrace.constants import _ORIGIN_KEY
from ddtrace.constants import _SAMPLING_PRIORITY_KEY
from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME
from tests.tracer.utils_inferred_spans.test_helpers import assert_web_and_inferred_aws_api_gateway_span_data
from tests.utils import TracerTestCase
from tests.webclient import Client

from .utils import PyramidBase
from .utils import PyramidTestCase


SERVER_PORT = 8000


def includeme(config):
    pass


class TestPyramid(PyramidTestCase):
    instrument = True

    def test_tween_overridden(self):
        # in case our tween is overridden by the user config we should
        # not log rendering
        self.override_settings({"pyramid.tweens": "pyramid.tweens.excview_tween_factory"})
        self.app.get("/json", status=200)
        spans = self.pop_spans()
        assert len(spans) == 0

    def test_http_request_header_tracing(self):
        config.pyramid.http.trace_headers(["my-header"])

        self.app.get(
            "/",
            headers={
                "my-header": "my_value",
            },
        )

        # validate it's traced
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.get_tag("component") == "pyramid"
        assert s.get_tag("span.kind") == "server"

        assert s.get_tag("http.request.headers.my-header") == "my_value"

    def test_http_response_header_tracing(self):
        config.pyramid.http.trace_headers(["my-response-header"])

        self.app.get("/")

        # validate it's traced
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]

        assert s.get_tag("http.response.headers.my-response-header") == "my_response_value"


class TestPyramidDistributedTracingDefault(PyramidBase):
    instrument = True

    def get_settings(self):
        return {}

    def test_distributed_tracing(self):
        # ensure the Context is properly created
        # if distributed tracing is enabled
        headers = {
            "x-datadog-trace-id": "100",
            "x-datadog-parent-id": "42",
            "x-datadog-sampling-priority": "2",
            "x-datadog-origin": "synthetics",
        }
        self.app.get("/", headers=headers, status=200)
        spans = self.pop_spans()
        assert len(spans) == 1
        # check the propagated Context
        span = spans[0]
        assert span.get_tag("component") == "pyramid"
        assert span.get_tag("span.kind") == "server"
        assert span.trace_id == 100
        assert span.parent_id == 42
        assert span.get_metric(_SAMPLING_PRIORITY_KEY) == 2
        assert span.get_tag(_ORIGIN_KEY) == "synthetics"

    def test_distributed_tracing_patterned(self):
        # ensure the Context is properly created
        # if distributed tracing is enabled
        headers = {
            "x-datadog-trace-id": "100",
            "x-datadog-parent-id": "42",
            "x-datadog-sampling-priority": "2",
            "x-datadog-origin": "synthetics",
        }
        self.app.get("/hello/world", headers=headers, status=200)
        spans = self.pop_spans()
        assert len(spans) == 1
        # check the propagated Context
        span = spans[0]
        assert span.get_tag("component") == "pyramid"
        assert span.get_tag("span.kind") == "server"
        assert span.get_tag("pyramid.route.name") == "hello_patterned"
        assert span.get_tag("http.route") == "/hello/{param}"
        assert span.trace_id == 100
        assert span.parent_id == 42
        assert span.get_metric(_SAMPLING_PRIORITY_KEY) == 2
        assert span.get_tag(_ORIGIN_KEY) == "synthetics"


class TestPyramidDistributedTracingDisabled(PyramidBase):
    instrument = True

    def get_settings(self):
        return {
            "datadog_distributed_tracing": False,
        }

    def test_distributed_tracing_disabled(self):
        # we do not inherit context if distributed tracing is disabled
        headers = {
            "x-datadog-trace-id": "100",
            "x-datadog-parent-id": "42",
            "x-datadog-sampling-priority": "2",
            "x-datadog-origin": "synthetics",
        }
        self.app.get("/", headers=headers, status=200)
        spans = self.pop_spans()
        assert len(spans) == 1
        # check the propagated Context
        span = spans[0]
        assert span.get_tag("component") == "pyramid"
        assert span.get_tag("span.kind") == "server"
        assert span.trace_id != 100
        assert span.parent_id != 42
        assert span.get_metric(_SAMPLING_PRIORITY_KEY) != 2
        assert span.get_tag(_ORIGIN_KEY) != "synthetics"


class TestSchematization(PyramidBase):
    instrument = True

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_schematized_service_name_default(self):
        self.app.get("/", status=200)
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "pyramid", "Expected 'pyramid' and got {}".format(s.service)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_service_name_v0(self):
        self.app.get("/", status=200)
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "pyramid", "Expected 'pyramid' and got {}".format(s.service)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_service_name_v1(self):
        self.app.get("/", status=200)
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "mysvc", "Expected 'mysvc' and got {}".format(s.service)

    @TracerTestCase.run_in_subprocess()
    def test_schematized_unspecified_service_name_default(self):
        self.app.get("/", status=200)
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "pyramid", "Expected 'pyramid' and got {}".format(s.service)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_unspecified_service_name_v0(self):
        self.app.get("/", status=200)
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == "pyramid", "Expected 'pyramid' and got {}".format(s.service)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_unspecified_service_name_v1(self):
        self.app.get("/", status=200)
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.service == DEFAULT_SPAN_SERVICE_NAME, "Expected '{}' and got {}".format(
            DEFAULT_SPAN_SERVICE_NAME, s.service
        )

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_operation_name_v0(self):
        self.app.get("/", status=200)
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.name == "pyramid.request", "Expected 'pyramid.request' and got {}".format(s.name)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_operation_name_v1(self):
        self.app.get("/", status=200)
        spans = self.pop_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.name == "http.server.request", "Expected 'http.server.request' and got {}".format(s.name)


@pytest.fixture
def pyramid_app():
    return "ddtrace-run python tests/contrib/pyramid/app/app.py"


@pytest.fixture(scope="function")
def pyramid_client(snapshot, pyramid_app):
    """Runs a Pyramid app in a subprocess and returns a client which can be used to query it.

    Traces are flushed by invoking a tracer.shutdown() using a /shutdown-tracer route
    at the end of the testcase.
    """

    env = os.environ.copy()
    env["SERVER_PORT"] = str(SERVER_PORT)

    # Create a temp folder as if run_function_from_file was used
    temp_dir = gettempdir()
    custom_temp_dir = os.path.join(temp_dir, "ddtrace_subprocess_dir")
    os.makedirs(custom_temp_dir, exist_ok=True)
    to_directory = custom_temp_dir + "/sample_app"

    # Swap out the file with the tmp file
    if "/app" in pyramid_app:
        from_directory = "tests/contrib/pyramid/app"
        pyramid_app = f"ddtrace-run python {to_directory}/app.py"
    else:
        from_directory = "tests/contrib/pyramid/pserve_app/"
        pyramid_app = f"ddtrace-run pserve {to_directory}/development.ini"

    # Copies the tests/contrib/pyramid/app or
    # tests/contrib/pyramid/pserve_app into this directory
    shutil.copytree(from_directory, to_directory, dirs_exist_ok=True)

    cmd = pyramid_app.split(" ")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        env=env,
    )

    client = Client("http://localhost:%d" % SERVER_PORT)

    # Wait for the server to start up
    client.wait()

    try:
        yield client
    finally:
        resp = client.get_ignored("/shutdown-tracer")
        assert resp.status_code == 200
        proc.terminate()

        # Clean up the temp directory
        if os.path.exists(custom_temp_dir):
            shutil.rmtree(custom_temp_dir)


# @pytest.mark.subprocess()
@pytest.mark.parametrize(
    "pyramid_app",
    [
        "ddtrace-run pserve tests/contrib/pyramid/pserve_app/development.ini",
        "ddtrace-run python tests/contrib/pyramid/app/app.py",
    ],
)
@pytest.mark.snapshot(ignores=["meta.http.useragent"])
def test_simple_pyramid_app_endpoint(pyramid_client):
    r = pyramid_client.get("/")
    assert r.status_code == 200


class TestAPIGatewayTracing(PyramidBase):
    """
    Ensure that Pyramid web applications are properly traced when API Gateway is involved
    """

    instrument = True

    def test_inferred_spans_api_gateway_default(self):
        # we do not inherit context if distributed tracing is disabled
        headers = {
            "x-dd-proxy": "aws-apigateway",
            "x-dd-proxy-request-time-ms": "1736973768000",
            "x-dd-proxy-path": "/",
            "x-dd-proxy-httpmethod": "GET",
            "x-dd-proxy-domain-name": "local",
            "x-dd-proxy-stage": "stage",
        }

        distributed_headers = {
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

        for setting_enabled in [False, True]:
            config._inferred_proxy_services_enabled = setting_enabled
            for test_headers in [distributed_headers, headers]:
                for test_endpoint in [
                    {
                        "endpoint": "/",
                        "status": 200,
                        "resource_name": "GET index",
                    },
                    {
                        "endpoint": "/error",
                        "status": 500,
                        "resource_name": "GET error",
                    },
                    {
                        "endpoint": "/exception",
                        "status": 500,
                        "resource_name": "GET exception",
                    },
                ]:
                    try:
                        self.app.get(test_endpoint["endpoint"], headers=test_headers, status=test_endpoint["status"])
                    except ZeroDivisionError:
                        # Passing because /exception raises a ZeroDivisionError but we still need to create spans
                        pass

                    spans = self.pop_spans()
                    if setting_enabled:
                        aws_gateway_span = spans[0]
                        web_span = spans[1]

                        assert_web_and_inferred_aws_api_gateway_span_data(
                            aws_gateway_span,
                            web_span,
                            web_span_name="pyramid.request",
                            web_span_component="pyramid",
                            web_span_service_name="pyramid",
                            web_span_resource=test_endpoint["resource_name"],
                            api_gateway_service_name="local",
                            api_gateway_resource="GET /",
                            method="GET",
                            status_code=test_endpoint["status"],
                            url="local/",
                            start=1736973768,
                        )
                    else:
                        web_span = spans[0]
                        assert web_span._parent is None
