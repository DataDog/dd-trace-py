import falcon as falcon
from falcon import testing

from tests.utils import DummyTracer

from .app import get_app


class DistributedTracingTestCase(testing.TestCase):
    """Executes tests using the manual instrumentation so a middleware
    is explicitly added.
    """

    def setUp(self):
        super(DistributedTracingTestCase, self).setUp()
        self._service = "falcon"
        self.tracer = DummyTracer()
        self.api = get_app(tracer=self.tracer)
        self.version = falcon.__version__
        if self.version[0] != "1":
            self.client = testing.TestClient(self.api)

    def test_distributred_tracing(self):
        headers = {
            "x-datadog-trace-id": "100",
            "x-datadog-parent-id": "42",
        }
        if self.version[0] == "1":
            out = self.simulate_get("/200", headers=headers)
            assert out.status_code == 200
        else:
            out = self.client.simulate_get("/200", headers=headers)
            assert out.status[:3] == "200"
        assert out.content.decode("utf-8") == "Success"

        traces = self.tracer.writer.pop_traces()

        assert len(traces) == 1
        assert len(traces[0]) == 1

        assert traces[0][0].parent_id == 42
        assert traces[0][0].trace_id == 100

    def test_distributred_tracing_disabled(self):
        self.tracer = DummyTracer()
        self.api = get_app(tracer=self.tracer, distributed_tracing=False)
        if self.version[0] != "1":
            self.client = testing.TestClient(self.api)
        headers = {
            "x-datadog-trace-id": "100",
            "x-datadog-parent-id": "42",
        }
        if self.version[0] == "1":
            out = self.simulate_get("/200", headers=headers)
            assert out.status_code == 200
        else:
            out = self.client.simulate_get("/200", headers=headers)
            assert out.status[:3] == "200"
        assert out.content.decode("utf-8") == "Success"

        traces = self.tracer.writer.pop_traces()

        assert len(traces) == 1
        assert len(traces[0]) == 1

        assert traces[0][0].parent_id != 42
        assert traces[0][0].trace_id != 100
