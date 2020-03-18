import django
import os
import pytest

from ddtrace import config, Pin
from ddtrace.contrib.django.conf import configure_from_settings

from tests.base import BaseTestCase


pytestmark = pytest.mark.skipif(
    "TEST_DATADOG_DJANGO_MIGRATION" not in os.environ, reason="test only relevant for migration"
)

"""
migration tests
"""


def test_configure_from_settings(tracer):
    pin = Pin.get_from(django)

    with BaseTestCase.override_config("django", dict()):
        assert "ddtrace.contrib.django" in django.conf.settings.INSTALLED_APPS
        assert hasattr(django.conf.settings, "DATADOG_TRACE")

        configure_from_settings(pin, config.django, django.conf.settings.DATADOG_TRACE)

        assert config.django.service_name == "django-test"
        assert config.django.cache_service_name == "cache-test"
        assert config.django.database_service_name_prefix == "db-test-"
        assert config.django.distributed_tracing_enabled is True
        assert config.django.instrument_databases is True
        assert config.django.instrument_caches is True
        assert config.django.analytics_enabled is True
        assert config.django.analytics_sample_rate is True
        # TODO: uncomment when figured out why setting this is not working
        # assert config.django.trace_query_string is True

        assert pin.tracer.enabled is True
        assert pin.tracer.tags["env"] == "env-test"
        assert pin.tracer.writer.api.hostname == "host-test"
        assert pin.tracer.writer.api.port == 1234
