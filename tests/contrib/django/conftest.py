import os

import django
from django.conf import settings
import mock
import os
import pytest

from ddtrace import Pin
from ddtrace.contrib.django import patch
from tests.utils import DummyTracer
from tests.utils import TracerSpanContainer


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

def setup_timeout_hook():
    mock.patch('django.core.handlers.base.BaseHandler.get_response', return_value=None)
    mock.patch('django.test.client.ClientHandler.get_response', return_value=None)

_django_hook_map = {
    "timeout": setup_timeout_hook
}
if os.environ.get("DJANGO_PREPATCH_HOOK"):
    value = os.environ.get("DJANGO_PREPATCH_HOOK")
    hook = _django_hook_map[value]
    hook()
    with open("/tmp/test.txt", "w+") as f:
        f.write(value + "\n")