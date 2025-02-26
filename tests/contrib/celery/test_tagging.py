import socket
import time

from celery import Celery
from celery.contrib.testing.worker import start_worker
import pytest

from ddtrace.contrib.internal.celery.patch import patch
from ddtrace.contrib.internal.celery.patch import unpatch
from ddtrace.trace import Pin
from tests.utils import DummyTracer

from .base import AMQP_BROKER_URL
from .base import BACKEND_URL
from .base import BROKER_URL


redis_celery_app = Celery(
    "mul_celery",
    broker=BROKER_URL,
    backend=BACKEND_URL,
)


@redis_celery_app.task
def multiply(x, y):
    return x * y


amqp_celery_app = Celery(
    "add_celery",
    broker=AMQP_BROKER_URL,
    backend="rpc://",
)


@amqp_celery_app.task
def add(x, y):
    return x + y


@pytest.fixture(autouse=False)
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


@pytest.fixture(autouse=False)
def traced_redis_celery_app(instrument_celery, dummy_tracer):
    Pin.get_from(redis_celery_app)
    Pin._override(redis_celery_app, tracer=dummy_tracer)
    yield redis_celery_app


@pytest.fixture(autouse=False)
def traced_amqp_celery_app(instrument_celery, dummy_tracer):
    Pin.get_from(amqp_celery_app)
    Pin._override(amqp_celery_app, tracer=dummy_tracer)
    yield amqp_celery_app


def test_redis_task(traced_redis_celery_app):
    tracer = Pin.get_from(traced_redis_celery_app).tracer

    with start_worker(
        traced_redis_celery_app,
        pool="solo",
        loglevel="info",
        perform_ping_check=False,
        shutdown_timeout=30,
    ):
        t = multiply.delay(4, 4)
        assert t.get(timeout=2) == 16

        # wait for spans to be received
        time.sleep(3)

        assert_traces(tracer, "multiply", t, 6379)


def test_amqp_task(instrument_celery, traced_amqp_celery_app):
    tracer = Pin.get_from(traced_amqp_celery_app).tracer

    with start_worker(
        traced_amqp_celery_app,
        pool="solo",
        loglevel="info",
        perform_ping_check=False,
        shutdown_timeout=30,
    ):
        t = add.delay(4, 4)
        assert t.get(timeout=30) == 8

        # wait for spans to be received
        time.sleep(3)

        assert_traces(tracer, "add", t, 5672)


def assert_traces(tracer, task_name, task, port):
    traces = tracer.pop_traces()

    assert 2 == len(traces)
    assert 1 == len(traces[0])
    assert 1 == len(traces[1])
    async_span = traces[0][0]
    run_span = traces[1][0]

    assert async_span.error == 0
    assert async_span.name == "celery.apply"
    assert async_span.resource == f"tests.contrib.celery.test_tagging.{task_name}"
    assert async_span.service == "celery-producer"
    assert async_span.get_tag("celery.id") == task.task_id
    assert async_span.get_tag("celery.action") == "apply_async"
    assert async_span.get_tag("celery.routing_key") == "celery"
    assert async_span.get_tag("component") == "celery"
    assert async_span.get_tag("span.kind") == "producer"
    assert async_span.get_tag("out.host") == "127.0.0.1"
    assert async_span.get_metric("network.destination.port") == port

    assert run_span.error == 0
    assert run_span.name == "celery.run"
    assert run_span.resource == f"tests.contrib.celery.test_tagging.{task_name}"
    assert run_span.service == "celery-worker"
    assert run_span.get_tag("celery.id") == task.task_id
    assert run_span.get_tag("celery.action") == "run"
    assert run_span.get_tag("component") == "celery"
    assert run_span.get_tag("span.kind") == "consumer"
    assert socket.gethostname() in run_span.get_tag("celery.hostname")


@pytest.fixture(autouse=False)
def list_broker_celery_app(instrument_celery, dummy_tracer):
    app = Celery("list_broker_celery", broker=BROKER_URL, backend=BACKEND_URL)
    # Set the broker URL to a list where the first URL is used for parsing
    app.conf.broker_url = [BROKER_URL, "memory://"]

    @app.task(name="tests.contrib.celery.test_tagging.subtract")
    def subtract(x, y):
        return x - y

    # Ensure the app is instrumented with the dummy tracer
    Pin.get_from(app)
    Pin._override(app, tracer=dummy_tracer)
    patch()
    yield app
    unpatch()


def test_list_broker_urls(list_broker_celery_app):
    """
    Test that when the broker URL is provided as a list,
    the instrumentation correctly parses the first URL in the list.
    """
    tracer = Pin.get_from(list_broker_celery_app).tracer

    with start_worker(
        list_broker_celery_app,
        pool="solo",
        loglevel="info",
        perform_ping_check=False,
        shutdown_timeout=30,
    ):
        t = list_broker_celery_app.tasks["tests.contrib.celery.test_tagging.subtract"].delay(10, 3)
        assert t.get(timeout=2) == 7

        # Allow time for the publish span to be recorded
        time.sleep(3)

        # assert_traces is assumed to be a helper function that checks that the
        # span has the expected values (e.g. target port is 6379 for BROKER_URL)
        assert_traces(tracer, "subtract", t, 6379)
