from unittest import mock

from ddtrace.trace import TraceFilter

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

    def test_template_service_source(self) -> None:
        with mock.patch("ddtrace.internal._service_state.is_user_provided_service", return_value=True):
            response = self.fetch("/template/")
        assert response.code == 200

        spans = self.get_spans()
        assert [span.name for span in spans] == ["tornado.request", "tornado.template"]
        for span in spans:
            assert span.service == "custom-tornado"
            assert span.get_tag("_dd.svc_src") == "m"
