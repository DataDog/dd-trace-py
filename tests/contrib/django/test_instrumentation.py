import time

# 3rd party
from nose.tools import eq_, ok_
from django.test import override_settings

# project
from ddtrace.contrib.django.conf import settings

# testing
from .utils import DjangoTraceTestCase


class DjangoInstrumentationTest(DjangoTraceTestCase):
    """
    Ensures that Django is correctly configured according to
    users settings
    """
    def test_tracer_flags(self):
        ok_(self.tracer.enabled)
        eq_(self.tracer.writer.api.hostname, 'localhost')
        eq_(self.tracer.writer.api.port, 7777)
        eq_(self.tracer.tags, {'env': 'test'})

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
