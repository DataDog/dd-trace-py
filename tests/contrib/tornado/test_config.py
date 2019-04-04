from nose.tools import eq_, ok_

from ddtrace.filters import FilterRequestsOnUrl

from .utils import TornadoTestCase


class TestTornadoSettings(TornadoTestCase):
    """
    Ensure that Tornado web Application configures properly
    the given tracer.
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
                    'FILTERS':  [
                        FilterRequestsOnUrl(r'http://test\.example\.com'),
                    ],
                },
            },
        }

    def test_tracer_is_properly_configured(self):
        # the tracer must be properly configured
        eq_(self.tracer.tags, {'env': 'production', 'debug': 'false'})
        eq_(self.tracer.enabled, False)
        eq_(self.tracer.writer.api.hostname, 'dd-agent.service.consul')
        eq_(self.tracer.writer.api.port, 8126)
        # settings are properly passed
        ok_(self.tracer.writer._filters is not None)
        eq_(len(self.tracer.writer._filters), 1)
        ok_(isinstance(self.tracer.writer._filters[0], FilterRequestsOnUrl))
