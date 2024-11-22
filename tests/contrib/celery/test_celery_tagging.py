import socket

from celery.contrib.testing.worker import start_worker

from ddtrace import Pin

from .main import add
from .main import multiply


def test_redis_task(traced_redis_celery_app):
    tracer = Pin.get_from(traced_redis_celery_app).tracer

    with start_worker(  # <-- Important!
        traced_redis_celery_app,
        pool="solo",
        loglevel="info",
        perform_ping_check=False,
        shutdown_timeout=30,  # <-- Important!
    ):
        t = multiply.delay(4, 4)
        assert t.get(timeout=2) == 16

        assert_traces(tracer, "multiply", t, "redis")


def test_amqp_task(traced_amqp_celery_app):
    tracer = Pin.get_from(traced_amqp_celery_app).tracer

    with start_worker(  # <-- Important!
        traced_amqp_celery_app,
        pool="solo",
        loglevel="info",
        perform_ping_check=False,
        shutdown_timeout=30,  # <-- Important!
    ):
        t = add.delay(4, 4)
        assert t.get(timeout=2) == 8

        assert_traces(tracer, "add", t, "amqp")


def assert_traces(tracer, task_name, task, backend):
    traces = tracer.pop_traces()

    assert 2 == len(traces)
    assert 1 == len(traces[0])
    assert 1 == len(traces[1])
    async_span = traces[0][0]
    run_span = traces[1][0]

    assert async_span.error == 0
    assert async_span.name == "celery.apply"
    assert async_span.resource == f"tests.contrib.celery.main.{task_name}"
    assert async_span.service == "celery-producer"
    assert async_span.get_tag("celery.id") == task.task_id
    assert async_span.get_tag("celery.action") == "apply_async"
    assert async_span.get_tag("celery.routing_key") == "celery"
    assert async_span.get_tag("component") == "celery"
    assert async_span.get_tag("span.kind") == "producer"
    assert async_span.get_tag("out.host") == "{backend}://127.0.0.1".format(backend=backend)

    assert run_span.error == 0
    assert run_span.name == "celery.run"
    assert run_span.resource == f"tests.contrib.celery.main.{task_name}"
    assert run_span.service == "celery-worker"
    assert run_span.get_tag("celery.id") == task.task_id
    assert run_span.get_tag("celery.action") == "run"
    assert run_span.get_tag("component") == "celery"
    assert run_span.get_tag("span.kind") == "consumer"
    assert socket.gethostname() in run_span.get_tag("celery.hostname")
