# 3rd party
from django.apps import apps
from django.test import TestCase, override_settings

# project
from ddtrace.tracer import Tracer
from ddtrace.contrib.django.conf import settings

# testing
from ...test_tracer import DummyWriter


class DjangoTracingDisabledTest(TestCase):
    def setUp(self):
        tracer = Tracer()
        tracer.writer = DummyWriter()
        self.tracer = tracer
        # Backup the old conf
        self.backupTracer = settings.TRACER
        self.backupEnabled = settings.ENABLED
        # Disable tracing
        settings.ENABLED = False
        settings.TRACER = tracer
        # Restart the app
        app = apps.get_app_config('datadog_django')
        app.ready()

    def tearDown(self):
        # Reset the original settings
        settings.ENABLED = self.backupEnabled
        settings.TRACER = self.backupTracer

    def test_no_service_info_is_written(self):
        services = self.tracer.writer.pop_services()
        assert len(services) == 0

    def test_no_trace_is_written(self):
        settings.TRACER.trace("client.testing").finish()
        traces = self.tracer.writer.pop_traces()
        assert len(traces) == 0
