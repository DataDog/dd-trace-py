from ddtrace.filters import TraceFilter
from ddtrace.tracer import Tracer
from tests.utils import DummyWriter

from .utils import TornadoTestCase


class TestFilter(TraceFilter):
    def process_trace(self, trace):
        if trace[0].name == "drop":
            return None
        else:
            return trace


class TestTornadoSettings(TornadoTestCase):
    """
    Ensure that Tornado web application properly configures the given tracer.
    """

    def get_app(self):
        # Override with a real tracer
        self.tracer = Tracer()
        super(TestTornadoSettings, self).get_app()

    def get_settings(self):
        # update tracer settings
        return {
            "datadog_trace": {
                "default_service": "custom-tornado",
                "tags": {"env": "production", "debug": "false"},
                "enabled": False,
                "agent_hostname": "dd-agent.service.consul",
                "agent_port": 8126,
                "settings": {
                    "FILTERS": [
                        TestFilter(),
                    ],
                },
            },
        }

    def test_tracer_is_properly_configured(self):
        # the tracer must be properly configured
        assert self.tracer._tags.get("env") == "production"
        assert self.tracer._tags.get("debug") == "false"
        assert self.tracer.enabled is False
        assert self.tracer.agent_trace_url == "http://dd-agent.service.consul:8126"

        writer = DummyWriter()
        self.tracer.configure(enabled=True, writer=writer)
        with self.tracer.trace("keep"):
            pass
        spans = writer.pop()
        assert len(spans) == 1

        with self.tracer.trace("drop"):
            pass
        spans = writer.pop()
        assert len(spans) == 0


class TestTornadoSettingsEnabled(TornadoTestCase):
    def get_settings(self):
        return {
            "datadog_trace": {
                "default_service": "custom-tornado",
                "enabled": True,
            },
        }

    def test_service(self):
        """Ensure that the default service for a Tornado web application is configured."""
        response = self.fetch("/success/")
        assert 200 == response.code

        spans = self.get_spans()
        assert 1 == len(spans)

        assert "custom-tornado" == spans[0].service
        assert "tornado.request" == spans[0].name
