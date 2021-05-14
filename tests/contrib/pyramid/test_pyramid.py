from ddtrace import config
from ddtrace.constants import ORIGIN_KEY
from ddtrace.constants import SAMPLING_PRIORITY_KEY

from .utils import PyramidBase
from .utils import PyramidTestCase


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
        assert span.trace_id == 100
        assert span.parent_id == 42
        assert span.get_metric(SAMPLING_PRIORITY_KEY) == 2
        assert span.get_tag(ORIGIN_KEY) == "synthetics"


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
        assert span.trace_id != 100
        assert span.parent_id != 42
        assert span.get_metric(SAMPLING_PRIORITY_KEY) != 2
        assert span.get_tag(ORIGIN_KEY) != "synthetics"
