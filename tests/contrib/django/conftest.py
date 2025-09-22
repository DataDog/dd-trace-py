import os

import django
from django.conf import settings
import pytest

from ddtrace._trace.pin import Pin
from ddtrace.contrib.internal.django.patch import patch
from tests.utils import DummyTracer
from tests.utils import TracerSpanContainer
from tests.utils import override_config


# We manually designate which settings we will be using in an environment variable
# This is similar to what occurs in the `manage.py`
if django.VERSION >= (2, 0, 0):
    app_name = "django_app"
else:
    app_name = "django1_app"
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.contrib.django.{0}.settings".format(app_name))


# `pytest` automatically calls this function once when tests are run.
def pytest_configure():
    settings.DEBUG = False
    patch()
    django.setup()


@pytest.fixture(autouse=True)
def clear_django_caches():
    """Automatically clear cached functions to avoid test pollution"""
    from ddtrace.contrib.internal.django import cache
    from ddtrace.contrib.internal.django import database

    cache.get_service_name.cache_clear()
    cache.func_cache_operation.cache_clear()
    database.get_conn_config.cache_clear()
    database.get_conn_service_name.cache_clear()
    database.get_traced_cursor_cls.cache_clear()


@pytest.fixture
def tracer():
    tracer = DummyTracer()
    # Patch Django and override tracer to be our test tracer
    pin = Pin.get_from(django)
    original_tracer = pin.tracer
    Pin._override(django, tracer=tracer)

    # Yield to our test
    with override_config("django", dict(_tracer=tracer)):
        yield tracer
    tracer.pop()

    # Reset the tracer pinned to Django and unpatch
    # DEV: unable to properly unpatch and reload django app with each test
    # unpatch()
    Pin._override(django, tracer=original_tracer)


@pytest.fixture
def test_spans(tracer):
    container = TracerSpanContainer(tracer)
    yield container
    container.reset()
