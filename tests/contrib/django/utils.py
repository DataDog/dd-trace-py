# 3rd party
from django.db import connections
from django.test import TestCase
from django.template import Template

# project
from ddtrace.tracer import Tracer
from ddtrace.contrib.django.settings import settings

# testing
from ...test_tracer import DummyWriter


# testing tracer
tracer = Tracer()
tracer.writer = DummyWriter()


class DjangoTraceTestCase(TestCase):
    """
    Base class that provides an internal tracer according to given
    Datadog settings. This class ensures that the tracer spans are
    properly reset after each run. The tracer is available in
    the ``self.tracer`` attribute.
    """
    def setUp(self):
        # assign the default tracer
        self.tracer = settings.DEFAULT_TRACER

    def tearDown(self):
        # empty the tracer spans
        self.tracer.writer.spans = []
