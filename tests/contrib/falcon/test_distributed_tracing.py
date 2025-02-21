from falcon import testing

from ddtrace import config
from ddtrace.constants import USER_KEEP
from ddtrace.contrib.internal.falcon.patch import FALCON_VERSION
from tests.tracer.utils_inferred_spans.test_helpers import assert_web_and_inferred_aws_api_gateway_span_data
from tests.utils import DummyTracer
from tests.utils import TracerTestCase

from .app import get_app
from .test_suite import FalconTestMixin


class DistributedTracingTestCase(testing.TestCase, FalconTestMixin, TracerTestCase):
    """Executes tests using the manual instrumentation so a middleware
    is explicitly added.
    """

    def setUp(self):
        super(DistributedTracingTestCase, self).setUp()
        self._service = "falcon"
        self.tracer = DummyTracer()
        self.api = get_app(tracer=self.tracer)
        if FALCON_VERSION >= (2, 0, 0):
            self.client = testing.TestClient(self.api)
        else:
            self.client = self

    def test_distributed_tracing_enabled(self):
        config.falcon["distributed_tracing"] = True
        headers = {
            "x-datadog-trace-id": "100",
            "x-datadog-parent-id": "42",
        }
        out = self.make_test_call("/200", headers=headers, expected_status_code=200)
        assert out.content.decode("utf-8") == "Success"

        traces = self.tracer.pop_traces()

        assert len(traces) == 1
        assert len(traces[0]) == 1

        assert traces[0][0].parent_id == 42
        assert traces[0][0].trace_id == 100

    def test_distributed_tracing_disabled_via_int_config(self):
        config.falcon["distributed_tracing"] = False
        self.tracer = DummyTracer()
        self.api = get_app(tracer=self.tracer)
        if FALCON_VERSION >= (2, 0, 0):
            self.client = testing.TestClient(self.api)
        headers = {
            "x-datadog-trace-id": "100",
            "x-datadog-parent-id": "42",
        }
        out = self.make_test_call("/200", headers=headers, expected_status_code=200)
        assert out.content.decode("utf-8") == "Success"

        traces = self.tracer.pop_traces()

        assert len(traces) == 1
        assert len(traces[0]) == 1

        assert traces[0][0].parent_id != 42
        assert traces[0][0].trace_id != 100

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_FALCON_DISTRIBUTED_TRACING="False"))
    def test_distributed_tracing_disabled_via_env_var(self):
        self.tracer = DummyTracer()
        self.api = get_app(tracer=self.tracer)

        if FALCON_VERSION >= (2, 0, 0):
            self.client = testing.TestClient(self.api)
        headers = {
            "x-datadog-trace-id": "100",
            "x-datadog-parent-id": "42",
        }
        out = self.make_test_call("/200", headers=headers, expected_status_code=200)
        assert out.content.decode("utf-8") == "Success"

        traces = self.tracer.pop_traces()
        assert len(traces) == 1
        assert len(traces[0]) == 1

        assert traces[0][0].parent_id != 42
        assert traces[0][0].trace_id != 100

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_INFERRED_PROXY_SERVICES_ENABLED="True"))
    def test_inferred_spans_api_gateway_distributed_tracing_enabled(self):
        config.falcon["distributed_tracing"] = True
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
        out = self.make_test_call("/200", headers=distributed_headers, expected_status_code=200)
        assert out.content.decode("utf-8") == "Success"

        traces = self.tracer.pop_traces()

        aws_gateway_span = traces[0][0]
        web_span = traces[0][1]

        assert len(traces) == 1
        assert len(traces[0]) == 2

        assert_web_and_inferred_aws_api_gateway_span_data(
            aws_gateway_span,
            web_span,
            web_span_name="falcon.request",
            web_span_component="falcon",
            web_span_service_name="falcon",
            web_span_resource="GET tests.contrib.falcon.app.resources.Resource200",
            api_gateway_service_name="local",
            api_gateway_resource="GET /",
            method="GET",
            status_code="200",
            url="local/",
            start=1736973768.0,
            is_distributed=True,
            distributed_trace_id=1,
            distributed_parent_id=2,
            distributed_sampling_priority=USER_KEEP,
        )

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_INFERRED_PROXY_SERVICES_ENABLED="False"))
    def test_inferred_spans_api_gateway_distributed_tracing_disabled(self):
        # When inferred proxy is disabled, there should be no inferred span
        config.falcon["distributed_tracing"] = True
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
        out = self.make_test_call("/200", headers=distributed_headers, expected_status_code=200)
        assert out.content.decode("utf-8") == "Success"

        traces = self.tracer.pop_traces()

        web_span = traces[0][0]
        assert web_span._parent is None
        assert web_span.trace_id == 1
