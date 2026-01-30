from ddtrace.trace import TraceFilter

from .utils import TornadoTestCase


class TestFilter(TraceFilter):
    def process_trace(self, trace):
        if trace[0].name == "drop":
            return None
        else:
            return trace


class TornadoSettingsTestCase(TornadoTestCase):
    """
    Base class for testing Tornado web application tracer configuration.
    Note: Named without 'Test' prefix to avoid pytest collecting it directly
    since it has no test methods - subclasses should define actual tests.
    """

    def get_app(self):
        super(TornadoSettingsTestCase, self).get_app()

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
