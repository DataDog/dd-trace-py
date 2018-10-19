# 3rd party
import django
from nose.tools import eq_, ok_

# project
from ddtrace.contrib.django.conf import settings, DatadogSettings
from ddtrace.contrib.django import patch

# testing
from .compat import reverse
from .utils import DjangoTraceTestCase
from ...util import set_env


class DjangoInstrumentationTest(DjangoTraceTestCase):
    """
    Ensures that Django is correctly configured according to
    users settings
    """
    def test_tracer_flags(self):
        ok_(self.tracer.enabled)
        eq_(self.tracer.writer.api.hostname, 'localhost')
        eq_(self.tracer.writer.api.port, 8126)
        eq_(self.tracer.tags, {'env': 'test'})

    def test_environment_vars(self):
        # Django defaults can be overridden by env vars, ensuring that
        # environment strings are properly converted
        with set_env(
                DATADOG_TRACE_AGENT_HOSTNAME='agent.consul.local',
                DATADOG_TRACE_AGENT_PORT='58126'):
            settings = DatadogSettings()
            eq_(settings.AGENT_HOSTNAME, 'agent.consul.local')
            eq_(settings.AGENT_PORT, 58126)

    def test_environment_var_wrong_port(self):
        # ensures that a wrong Agent Port doesn't crash the system
        # and defaults to 8126
        with set_env(DATADOG_TRACE_AGENT_PORT='something'):
            settings = DatadogSettings()
            eq_(settings.AGENT_PORT, 8126)

    def test_tracer_call(self):
        # test that current Django configuration is correct
        # to send traces to a real trace agent
        tracer = settings.TRACER
        tracer.trace('client.testing').finish()
        trace = self.tracer.writer.pop()
        traces = [trace]

        response = tracer.writer.api.send_traces(traces)
        ok_(response)
        eq_(response.status, 200)

    def test_idempotent_patch(self):
        patch()
        django.setup()

        # ensures that the internals are properly traced
        url = reverse('users-list')
        response = self.client.get(url)
        eq_(response.status_code, 200)

        # check that spans are not duplicated
        spans = self.tracer.writer.pop()
        eq_(len(spans), 3)
        sp_request = spans[0]
        sp_template = spans[1]
        sp_database = spans[2]
        eq_(sp_database.get_tag('django.db.vendor'), 'sqlite')
        eq_(sp_template.get_tag('django.template_name'), 'users_list.html')
        eq_(sp_request.get_tag('http.status_code'), '200')
        eq_(sp_request.get_tag('http.url'), '/users/')
        eq_(sp_request.get_tag('django.user.is_authenticated'), 'False')
        eq_(sp_request.get_tag('http.method'), 'GET')
        eq_(sp_request.span_type, 'http')
