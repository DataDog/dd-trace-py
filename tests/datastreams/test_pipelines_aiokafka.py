"""Automatic DSM pipelines recording for aiokafka (DSM2-531, T-5).

The handlers in ``ddtrace.internal.datastreams.aiokafka`` record topic sides
and in-process links through the pinned ``PipelinesReporter`` API
(CONTRACT.md §1). These tests drive the handlers directly and via
``core.dispatch`` with fake objects mirroring the shapes the aiokafka contrib
patch emits, then inspect ``reporter.take_report("svc", "env")``.
"""

import asyncio
import threading

import pytest

import ddtrace.internal.datastreams.pipelines as pipelines_module
from ddtrace.internal import core
from ddtrace.internal.datastreams.aiokafka import pipelines_aiokafka_many_messages_consume
from ddtrace.internal.datastreams.aiokafka import pipelines_aiokafka_message_consume
from ddtrace.internal.datastreams.aiokafka import pipelines_aiokafka_send_start


class FakeEvent:
    def __init__(self, bootstrap_servers):
        self.bootstrap_servers = bootstrap_servers


class FakeExecutionContext:
    def __init__(self, bootstrap_servers=None):
        self.event = FakeEvent(bootstrap_servers)


class FakeClient:
    def __init__(self, bootstrap_servers):
        self._bootstrap_servers = bootstrap_servers


class FakeConsumer:
    def __init__(self, bootstrap_servers="localhost:9092", group_id="orders-group"):
        self._client = FakeClient(bootstrap_servers)
        self._group_id = group_id


class FakeMessage:
    def __init__(self, topic):
        self.topic = topic
        self.partition = 0
        self.offset = 0


@pytest.fixture
def reporter(monkeypatch):
    """A fresh process-level PipelinesReporter, forced to be enabled."""
    monkeypatch.setenv("DD_DATA_STREAMS_PIPELINES_ENABLED", "true")
    monkeypatch.setattr(pipelines_module, "_reporter", None)
    r = pipelines_module.get_pipelines_reporter()
    assert r is not None, "pipelines reporting defaults to enabled"
    return r


def take(reporter):
    report = reporter.take_report("svc", "env")
    assert report is not None, "expected at least one side to be recorded"
    return report


def _send_start(topic, bootstrap_servers="localhost:9092"):
    pipelines_aiokafka_send_start(topic, None, None, [], FakeExecutionContext(bootstrap_servers), None)


def _getone(consumer, topic):
    pipelines_aiokafka_message_consume(consumer, None, None, FakeMessage(topic), None)


def test_produce_records_side_with_empty_group(reporter):
    """A bare produce records direction 0, group "" and a source link (T-5)."""
    _send_start("orders", "localhost:9092")
    report = take(reporter)
    assert report["sides"] == [[0, "kafka", "localhost:9092", "orders", ""]]
    # No consume context on this thread: the produce is a source.
    assert report["links"] == [[-1, 0]]


def test_consume_records_side_with_group(reporter):
    """A consume records direction 1 with the consumer's group (T-5)."""
    _getone(FakeConsumer("localhost:9092", "orders-group"), "orders")
    report = take(reporter)
    assert report["sides"] == [[1, "kafka", "localhost:9092", "orders", "orders-group"]]
    assert report["links"] == []


def test_consume_then_produce_links_in_same_thread(reporter):
    """A produce inside a consume context links to it, same thread (T-3/T-5)."""
    _getone(FakeConsumer("localhost:9092", "orders-group"), "orders")
    _send_start("enriched-orders", "localhost:9092")
    report = take(reporter)
    # Sides are sorted: the produce side comes first.
    assert report["sides"] == [
        [0, "kafka", "localhost:9092", "enriched-orders", ""],
        [1, "kafka", "localhost:9092", "orders", "orders-group"],
    ]
    assert report["links"] == [[1, 0]]


def test_consume_then_produce_links_inside_asyncio_task(reporter):
    """Consume and produce in the same asyncio task link (T-3/T-5, demo shape)."""

    async def process_message():
        _getone(FakeConsumer("localhost:9092", "orders-group"), "orders")
        await asyncio.sleep(0)
        # simulate the handler reacting to the message by producing
        _send_start("enriched-orders", "localhost:9092")

    asyncio.run(process_message())
    report = take(reporter)
    assert report["sides"] == [
        [0, "kafka", "localhost:9092", "enriched-orders", ""],
        [1, "kafka", "localhost:9092", "orders", "orders-group"],
    ]
    assert report["links"] == [[1, 0]]


def test_produce_after_context_replaced_links_to_new_consume(reporter):
    """A newer consume replaces the context; the produce links to it only (T-4)."""
    consumer = FakeConsumer("localhost:9092", "orders-group")
    _getone(consumer, "topic-a")
    _getone(consumer, "topic-b")
    _send_start("enriched", "localhost:9092")
    report = take(reporter)
    assert report["sides"] == [
        [0, "kafka", "localhost:9092", "enriched", ""],
        [1, "kafka", "localhost:9092", "topic-a", "orders-group"],
        [1, "kafka", "localhost:9092", "topic-b", "orders-group"],
    ]
    # topic-a's context is gone (replaced): only the topic-b link is recorded.
    assert report["links"] == [[2, 0]]


def test_produce_on_another_thread_is_source(reporter):
    """The current-consume context is thread-local: another thread's produce is a source (T-4)."""
    _getone(FakeConsumer("localhost:9092", "orders-group"), "orders")

    produced = threading.Event()

    def produce_on_other_thread():
        _send_start("enriched", "localhost:9092")
        produced.set()

    thread = threading.Thread(target=produce_on_other_thread)
    thread.start()
    assert produced.wait(timeout=5)
    thread.join()

    report = take(reporter)
    assert report["sides"] == [
        [0, "kafka", "localhost:9092", "enriched", ""],
        [1, "kafka", "localhost:9092", "orders", "orders-group"],
    ]
    assert report["links"] == [[-1, 0]]


def test_consume_group_unset_uses_empty_string(reporter):
    """A consumer without a group_id records group "" (T-5)."""
    _getone(FakeConsumer("localhost:9092", None), "orders")
    report = take(reporter)
    assert report["sides"] == [[1, "kafka", "localhost:9092", "orders", ""]]


def test_bootstrap_servers_list_joined_into_cluster(reporter):
    """A list of bootstrap servers is normalized to a comma-joined cluster (T-5)."""
    consumer = FakeConsumer(["kafka-1:9092", "kafka-2:9092"], "orders-group")
    _getone(consumer, "orders")
    _send_start("enriched", ["kafka-1:9092", "kafka-2:9092"])
    report = take(reporter)
    assert report["sides"] == [
        [0, "kafka", "kafka-1:9092,kafka-2:9092", "enriched", ""],
        [1, "kafka", "kafka-1:9092,kafka-2:9092", "orders", "orders-group"],
    ]
    assert report["links"] == [[1, 0]]


def test_getmany_records_each_message_and_last_is_current(reporter):
    """getmany records one consume side per message; the last one is current (T-5)."""
    consumer = FakeConsumer("localhost:9092", "orders-group")
    messages = {
        "partition-0": [FakeMessage("topic-a"), FakeMessage("topic-b")],
        "partition-1": [FakeMessage("topic-c")],
    }
    pipelines_aiokafka_many_messages_consume(consumer, None, messages)
    _send_start("enriched", "localhost:9092")
    report = take(reporter)
    assert report["sides"] == [
        [0, "kafka", "localhost:9092", "enriched", ""],
        [1, "kafka", "localhost:9092", "topic-a", "orders-group"],
        [1, "kafka", "localhost:9092", "topic-b", "orders-group"],
        [1, "kafka", "localhost:9092", "topic-c", "orders-group"],
    ]
    # The last consumed message (topic-c) is the thread's current context.
    assert report["links"] == [[3, 0]]


def test_getmany_without_messages_records_nothing(reporter):
    pipelines_aiokafka_many_messages_consume(FakeConsumer(), None, None)
    assert reporter.take_report("svc", "env") is None


def test_getone_without_message_records_nothing(reporter):
    pipelines_aiokafka_message_consume(FakeConsumer(), None, None, None, None)
    assert reporter.take_report("svc", "env") is None


def test_core_dispatch_reaches_pipelines_handlers(reporter):
    """The handlers are subscribed on the aiokafka core events at import (T-5)."""
    core.dispatch(
        "aiokafka.getone.message",
        (FakeConsumer("localhost:9092", "orders-group"), None, 0, FakeMessage("orders"), None),
    )
    core.dispatch("aiokafka.send.start", ("enriched", None, None, [], FakeExecutionContext("localhost:9092"), None))
    report = take(reporter)
    assert report["sides"] == [
        [0, "kafka", "localhost:9092", "enriched", ""],
        [1, "kafka", "localhost:9092", "orders", "orders-group"],
    ]
    assert report["links"] == [[1, 0]]


def test_disabled_reporter_makes_handlers_noop(monkeypatch):
    """With no reporter, the handlers return without recording or raising (T-5, T-13)."""
    import ddtrace.internal.datastreams.aiokafka as aiokafka_module

    monkeypatch.setattr(aiokafka_module, "get_pipelines_reporter", lambda: None)
    _send_start("orders", "localhost:9092")
    _getone(FakeConsumer(), "orders")
    pipelines_aiokafka_many_messages_consume(FakeConsumer(), None, {"p": [FakeMessage("orders")]})


def test_pipelines_disabled_returns_no_reporter(monkeypatch):
    """DD_DATA_STREAMS_PIPELINES_ENABLED=false disables the reporter (T-5)."""
    # Settings are resolved at import, so disable via the resolved attribute
    # (the env var form is covered by the core suite's subprocess tests).
    monkeypatch.setattr(pipelines_module, "_reporter", None)
    monkeypatch.setattr(pipelines_module.config, "_data_streams_pipelines_enabled", False)
    assert pipelines_module.get_pipelines_reporter() is None


def test_handlers_never_raise(reporter):
    """Broken inputs are swallowed, never raised into the application (T-13)."""
    # No _client/_group_id attributes on the instance.
    pipelines_aiokafka_message_consume(object(), None, None, FakeMessage("orders"), None)
    # No event/span context on the producer side.
    pipelines_aiokafka_send_start("orders", None, None, [], None, None)
    # Non-string, non-iterable bootstrap servers.
    pipelines_aiokafka_send_start("orders", None, None, [], FakeExecutionContext(42), None)
    pipelines_aiokafka_message_consume(FakeConsumer(42, None), None, None, FakeMessage("orders"), None)
    # Whatever was recordable got recorded; nothing raised into the caller.
    assert reporter.take_report("svc", "env") is not None


def test_take_report_resets_window(reporter):
    """take_report resets the window: a second take with no traffic is None (T-5)."""
    _send_start("orders", "localhost:9092")
    assert take(reporter)["sides"] == [[0, "kafka", "localhost:9092", "orders", ""]]
    assert reporter.take_report("svc", "env") is None
