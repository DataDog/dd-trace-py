import os
import django
from django.conf import settings
import pytest

from ddtrace import Pin
from ddtrace.contrib.django import patch, unpatch

from ...utils.span import TracerSpanContainer
from ...utils.tracer import DummyTracer


# We manually designate which settings we will be using in an environment variable
# This is similar to what occurs in the `manage.py`
if django.VERSION >= (2, 0, 0):
    app_name = 'testapp_2'
else:
    app_name = 'testapp_1'
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tests.contrib.django.{0}.settings'.format(app_name))


# `pytest` automatically calls this function once when tests are run.
def pytest_configure():
    settings.DEBUG = False
    # If we were testing < 1.7.0 we would need to call `settings.configure()` instead of `django.setup()`
    django.setup()


@pytest.fixture(autouse=True)
def patch_django(tracer):
    # Patch Django and override tracer to be our test tracer
    patch()
    pin = Pin.get_from(django)
    original_tracer = pin.tracer
    Pin.override(django, tracer=tracer)

    # Yield to our test
    yield

    # Reset the tracer pinned to Django and unpatch
    Pin.override(django, tracer=original_tracer)
    unpatch()


@pytest.fixture
def tracer():
    tracer = DummyTracer()
    yield tracer
    tracer.writer.pop()


@pytest.fixture
def test_spans(tracer):
    container = TracerSpanContainer(tracer)
    yield container
    container.reset()
