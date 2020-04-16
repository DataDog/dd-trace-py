from ddtrace.filters import FilterRequestsOnUrl

from .utils import TornadoTestCase


class TestTornadoSettings(TornadoTestCase):
    """
    Ensure that Tornado web application properly configures the given tracer.
    """
    def get_settings(self):
        # update tracer settings
        return {
            'datadog_trace': {
                'default_service': 'custom-tornado',
                'tags': {'env': 'production', 'debug': 'false'},
                'enabled': False,
                'agent_hostname': 'dd-agent.service.consul',
                'agent_port': 8126,
                'settings': {
                    'FILTERS': [
                        FilterRequestsOnUrl(r'http://test\.example\.com'),
                    ],
                },
            },
        }

    def test_tracer_is_properly_configured(self):
        # the tracer must be properly configured
        assert self.tracer.tags == {'env': 'production', 'debug': 'false'}
        assert self.tracer.enabled is False
        assert self.tracer.writer.api.hostname == 'dd-agent.service.consul'
        assert self.tracer.writer.api.port == 8126
        # settings are properly passed
        assert self.tracer.writer._filters is not None
        assert len(self.tracer.writer._filters) == 1
        assert isinstance(self.tracer.writer._filters[0], FilterRequestsOnUrl)


class TestTornadoSettingsEnabled(TornadoTestCase):
    def get_settings(self):
        return {
            'datadog_trace': {
                'default_service': 'custom-tornado',
                'enabled': True,
            },
        }

    def test_service(self):
        """Ensure that the default service for a Tornado web application is configured."""
        response = self.fetch('/success/')
        assert 200 == response.code

        spans = self.get_spans()
        assert 1 == len(spans)

        assert 'custom-tornado' == spans[0].service
        assert 'tornado.request' == spans[0].name
