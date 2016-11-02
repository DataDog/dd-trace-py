import time

# 3rd party
from nose.tools import eq_, ok_
from django.test import override_settings

# testing
from .utils import DjangoTraceTestCase


class DjangoInstrumentationTest(DjangoTraceTestCase):
    """
    Ensures that Django is correctly configured according to
    users settings
    """
    def test_enabled_flag(self):
        eq_(self.tracer.writer.api.hostname, 'agent.service.consul')
        eq_(self.tracer.writer.api.port, '8777')
