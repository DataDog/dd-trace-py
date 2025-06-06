import logging
import os

import django
from django.conf import settings
import pytest

from ddtrace.contrib.internal.django.patch import patch
from ddtrace.trace import Pin
from tests.utils import DummyTracer
from tests.utils import TracerSpanContainer


log = logging.getLogger(__name__)


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
    Pin._override(django, tracer=tracer)

    # Yield to our test
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


def is_the_test_database_there(when):
    from psycopg2 import connect

    conn = None
    try:
        conn = connect(
            **{"host": "127.0.0.1", "port": 5432, "user": "postgres", "password": "postgres", "dbname": "test_postgres"}
        )
        conn.cursor().execute("SELECT 1;")
        print(f"\nꙮꙮꙮ test_postgres database at {when} YES\n")
    except Exception as e:
        print(f"\nꙮꙮꙮ test_postgres database at {when} NO: {e}\n")
    finally:
        if conn:
            conn.close()


@pytest.fixture(autouse=True)
def check_is_the_test_database_there():
    is_the_test_database_there("setup")
    yield
    is_the_test_database_there("teardown")
