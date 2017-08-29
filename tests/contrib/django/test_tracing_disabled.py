# 3rd party
from django.apps import apps
from django.test import TestCase, override_settings

# project
from ddtrace.tracer import Tracer
from ddtrace.contrib.django.conf import settings

# testing
from ...test_tracer import DummyWriter


class DjangoTracingDisabledTest(TestCase):
    def test_nothing_is_written(self):
        tracer = Tracer()
        tracer.writer = DummyWriter()
        # Backup the old conf
        backupTracer = settings.TRACER
        backupEnabled = settings.ENABLED
        # Disable tracing
        settings.ENABLED = False
        settings.TRACER = tracer
        # Restart the app
        app = apps.get_app_config('datadog_django')
        app.ready()

        traces = tracer.writer.pop_traces()
        assert len(traces) == 0
        services = tracer.writer.pop_services()
        assert len(services) == 0

        # Reset the original settings
        settings.ENABLED = backupEnabled
        settings.TRACER = backupTracer
