import os

import django
from django.conf import settings
import pytest

from ddtrace import Pin
from ddtrace.contrib.internal.django.patch import patch
from tests.utils import DummyTracer
from tests.utils import TracerSpanContainer


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.appsec.integrations.django_tests.django_app.settings")


# `pytest` automatically calls this function once when tests are run.
def pytest_configure():
    settings.DEBUG = False
    patch()
    django.setup()


@pytest.fixture
def tracer():
    tracer = DummyTracer()
    # Patch Django and override tracer to be our test tracer
    pin = Pin.get_from(django)
    original_tracer = pin.tracer
    Pin.override(django, tracer=tracer)

    # Yield to our test
    yield tracer
    tracer.pop()

    # Reset the tracer pinned to Django and unpatch
    # DEV: unable to properly unpatch and reload django app with each test
    # unpatch()
    Pin.override(django, tracer=original_tracer)


@pytest.fixture
def test_spans(tracer):
    container = TracerSpanContainer(tracer)
    yield container
    container.reset()
