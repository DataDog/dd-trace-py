from falcon import testing

from ddtrace import config
from ddtrace.contrib.falcon.patch import FALCON_VERSION
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
