"""Tests for the automatic DSM pipelines kombu hooks (DSM2-531).

Covers TEST doc T-5 (kombu/RabbitMQ sides and in-process links), plus the T-3
(produce links to the thread's current consume) and T-4 (context replaced by
the next consume) semantics where natural. Handlers are driven directly and
through ddtrace.internal.core.dispatch with the same fake objects the existing
datastreams tests use.
"""

import threading

import pytest

from ddtrace.internal.datastreams.kombu import handle_kombu_consume_pipelines
from ddtrace.internal.datastreams.kombu import handle_kombu_produce_pipelines
import ddtrace.internal.datastreams.pipelines as pipelines_module
from ddtrace.internal.datastreams.pipelines import PipelinesReporter


class FakeQueue:
    def __init__(self, name):
        self.name = name


class FakeConsumer:
    def __init__(self, queues):
        self.queues = queues


class FakeMessage:
    def __init__(self, body=b"body", headers=None):
        self.body = body
        self.headers = headers or {}


def make_publish_args(routing_key=None, exchange=None):
    # Indices used by the kombu utils: 0 body, 4 headers, 6 routing key, 9 exchange.
    return (
        b"body",  # body (PUBLISH_BODY_IDX)
        None,  # priority
        None,  # content_type
        None,  # content_encoding
        {},  # headers (HEADER_POS)
        None,  # properties
        routing_key,
        None,  # mandatory
        None,  # immediate
        exchange,
    )


@pytest.fixture
def reporter(monkeypatch):
    # Fresh process-level reporter so every test starts from an empty window.
    fresh = PipelinesReporter()
    monkeypatch.setattr(pipelines_module, "_reporter", fresh)
    return fresh


def test_produce_side_values_default_exchange(reporter):
    # T-5: on the default exchange the routing key (destination queue name) is the topic.
    handle_kombu_produce_pipelines(make_publish_args(routing_key="orders"), {}, None)

    report = reporter.take_report("svc", "env")
    assert report["sides"] == [[0, "rabbitmq", "", "orders", ""]]
    # No current consume on this thread: the produce is a source link.
    assert report["links"] == [[-1, 0]]


def test_produce_side_values_named_exchange(reporter):
    # T-5: a named exchange is the topic.
    handle_kombu_produce_pipelines(make_publish_args(routing_key="orders", exchange="billing"), {}, None)

    report = reporter.take_report("svc", "env")
    assert report["sides"] == [[0, "rabbitmq", "", "billing", ""]]
    assert report["links"] == [[-1, 0]]


def test_produce_skipped_without_exchange_and_routing_key(reporter):
    handle_kombu_produce_pipelines(make_publish_args(routing_key=None, exchange=None), {}, None)

    assert reporter.take_report("svc", "env") is None


def test_consume_side_values(reporter):
    # T-5: consume side with the queue name as topic and group.
    handle_kombu_consume_pipelines(FakeConsumer([FakeQueue("orders")]), FakeMessage(), None)

    report = reporter.take_report("svc", "env")
    assert report["sides"] == [[1, "rabbitmq", "", "orders", "orders"]]
    assert report["links"] == []


def test_consume_with_no_queues_is_noop(reporter):
    handle_kombu_consume_pipelines(FakeConsumer([]), FakeMessage(), None)

    assert reporter.take_report("svc", "env") is None


def test_link_produce_inside_consume_context(reporter):
    # T-3: a produce on the same thread after a consume links to that consume.
    handle_kombu_consume_pipelines(FakeConsumer([FakeQueue("orders")]), FakeMessage(), None)
    handle_kombu_produce_pipelines(make_publish_args(routing_key="payments"), {}, None)

    report = reporter.take_report("svc", "env")
    assert report["sides"] == [
        [0, "rabbitmq", "", "payments", ""],
        [1, "rabbitmq", "", "orders", "orders"],
    ]
    assert report["links"] == [[1, 0]]


def test_consume_context_replaced_by_next_consume(reporter):
    # T-4: the consumed message's context is gone once a later consume replaces it.
    handle_kombu_consume_pipelines(FakeConsumer([FakeQueue("orders")]), FakeMessage(), None)
    handle_kombu_consume_pipelines(FakeConsumer([FakeQueue("billing")]), FakeMessage(), None)
    handle_kombu_produce_pipelines(make_publish_args(routing_key="payments"), {}, None)

    report = reporter.take_report("svc", "env")
    assert report["sides"] == [
        [0, "rabbitmq", "", "payments", ""],
        [1, "rabbitmq", "", "billing", "billing"],
        [1, "rabbitmq", "", "orders", "orders"],
    ]
    # Only the newer consume ("billing") is the in-side of the produce.
    assert report["links"] == [[1, 0]]


def test_produce_on_other_thread_is_source(reporter):
    # T-3: the consume context is thread-local; a produce on another thread is a source.
    handle_kombu_consume_pipelines(FakeConsumer([FakeQueue("orders")]), FakeMessage(), None)

    errors = []

    def produce_on_other_thread():
        try:
            handle_kombu_produce_pipelines(make_publish_args(routing_key="payments"), {}, None)
        except Exception as e:  # noqa: BLE001 - surface into the test thread
            errors.append(e)

    t = threading.Thread(target=produce_on_other_thread)
    t.start()
    t.join()
    assert not errors

    report = reporter.take_report("svc", "env")
    assert report["sides"] == [
        [0, "rabbitmq", "", "payments", ""],
        [1, "rabbitmq", "", "orders", "orders"],
    ]
    assert report["links"] == [[-1, 0]]


def test_handlers_never_raise(reporter):
    # T-13-ish safety: malformed args must not raise into application code.
    handle_kombu_produce_pipelines((), {}, None)
    handle_kombu_consume_pipelines(None, None, None)

    assert reporter.take_report("svc", "env") is None


def test_handlers_noop_when_feature_disabled(monkeypatch):
    # Kill switch: get_pipelines_reporter() returns None and the handlers do nothing.
    monkeypatch.setattr(pipelines_module, "_pipelines_enabled", lambda: False)
    monkeypatch.setattr(pipelines_module, "_reporter", None)

    handle_kombu_produce_pipelines(make_publish_args(routing_key="orders"), {}, None)
    handle_kombu_consume_pipelines(FakeConsumer([FakeQueue("orders")]), FakeMessage(), None)

    assert pipelines_module.get_pipelines_reporter() is None


def test_handlers_subscribed_via_core_dispatch(reporter):
    # Wiring: the handlers fire on the contrib patch events, unconditionally at import.
    from ddtrace.internal import core
    from ddtrace.internal.datastreams import kombu as kombu_hooks

    core.dispatch("kombu.amqp.publish.pre", (make_publish_args(routing_key="orders"), {}, None))
    core.dispatch(
        "kombu.amqp.receive.post",
        (FakeConsumer([FakeQueue("billing")]), FakeMessage(), None),
    )

    report = reporter.take_report("svc", "env")
    assert report["sides"] == [
        [0, "rabbitmq", "", "orders", ""],
        [1, "rabbitmq", "", "billing", "billing"],
    ]
    # The dispatch order is publish then receive, so the produce has no consume parent.
    assert report["links"] == [[-1, 0]]


@pytest.mark.subprocess(env={"DD_DATA_STREAMS_PIPELINES_ENABLED": "false"})
def test_disabled_via_env_subprocess():
    from ddtrace.internal import core
    from ddtrace.internal.datastreams import kombu as kombu_hooks  # noqa: F401  (registers hooks)
    from ddtrace.internal.datastreams.pipelines import get_pipelines_reporter

    assert get_pipelines_reporter() is None

    # Dispatching the events must be a no-op and never raise.
    args = (
        b"body",
        None,
        None,
        None,
        {},
        None,
        "orders",
        None,
        None,
        None,
    )

    class FakeQueue:
        name = "orders"

    class FakeConsumer:
        queues = [FakeQueue()]

    core.dispatch("kombu.amqp.publish.pre", (args, {}, None))
    core.dispatch("kombu.amqp.receive.post", (FakeConsumer(), None, None))
