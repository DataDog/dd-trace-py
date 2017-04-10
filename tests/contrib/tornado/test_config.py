from nose.tools import eq_

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
                'agent_port': 58126,
            },
        }

    def test_tracer_is_properly_configured(self):
        # the tracer must be properly configured
        eq_(self.tracer._services, {'custom-tornado': ('custom-tornado', 'tornado', 'web')})
        eq_(self.tracer.tags, {'env': 'production', 'debug': 'false'})
        eq_(self.tracer.enabled, False)
        eq_(self.tracer.writer.api.hostname, 'dd-agent.service.consul')
        eq_(self.tracer.writer.api.port, 58126)
