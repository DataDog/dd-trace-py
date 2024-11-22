import pytest

from ddtrace import Pin
from ddtrace.contrib.celery import patch
from ddtrace.contrib.celery import unpatch
from tests.utils import DummyTracer

from .base import BROKER_URL
from .main import amqp_celery_app
from .main import redis_celery_app


pytest_plugins = ("celery.contrib.pytest",)  # <-- Important!


@pytest.fixture(autouse=True)
def instrument_celery():
    # Instrument Celery and create an app with Broker and Result backends
    patch()
    yield
    # Remove instrumentation from Celery
    unpatch()


@pytest.fixture(scope="session")
def celery_config():
    return {"broker_url": BROKER_URL}


@pytest.fixture
def dummy_tracer():
    return DummyTracer()


@pytest.fixture(autouse=True)
def traced_redis_celery_app(dummy_tracer):
    Pin.get_from(redis_celery_app)
    Pin.override(redis_celery_app, tracer=dummy_tracer)
    yield redis_celery_app


@pytest.fixture(autouse=True)
def traced_amqp_celery_app(dummy_tracer):
    Pin.get_from(amqp_celery_app)
    Pin.override(amqp_celery_app, tracer=dummy_tracer)
    yield amqp_celery_app
