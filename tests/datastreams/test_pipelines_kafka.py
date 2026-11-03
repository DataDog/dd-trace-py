"""Automatic DSM pipelines (DSM2-531) kafka hooks: sides and in-process links.

Drives the pipelines recording handlers in
``ddtrace.internal.datastreams.kafka`` directly and through
``ddtrace.internal.core.dispatch`` with fake confluent-kafka objects, then
asserts the recorded sides and links via ``PipelinesReporter.take_report``.

TEST doc coverage: T-5 (kafka system), plus T-3 (link produce inside consume
context) and T-4 (consumed message context replaced) semantics where natural.
"""

import threading

import pytest

from ddtrace.internal import core
from ddtrace.internal.datastreams.kafka import pipelines_kafka_message_produce
import ddtrace.internal.datastreams.pipelines as pipelines_module
from ddtrace.internal.datastreams.pipelines import get_pipelines_reporter


class FakeMessage:
    def __init__(self, topic, error=None):
        self._topic = topic
        self._error = error

    def topic(self):
        return self._topic

    def error(self):
        return self._error


class FakeProducer:
    def __init__(self, bootstrap_servers=None, cluster_id=None):
        self._dd_bootstrap_servers = bootstrap_servers
        self._dd_cluster_id = cluster_id


class FakeConsumer:
    def __init__(self, bootstrap_servers=None, cluster_id=None, group_id="group"):
        self._dd_bootstrap_servers = bootstrap_servers
        self._dd_cluster_id = cluster_id
        self._group_id = group_id


class BrokenMessage:
    def error(self):
        return None

    def topic(self):
        raise RuntimeError("broken message")


@pytest.fixture(autouse=True)
def fresh_reporter():
    """Isolate each test with a brand-new process-level pipelines reporter."""
    pipelines_module._reporter = None
    reporter = get_pipelines_reporter()
    assert reporter is not None
    yield reporter
    pipelines_module._reporter = None


def _dispatch_produce(producer, topic, cluster_id=...):
    """Dispatch kafka.produce.start with the kafka context items the patch sets."""
    # The root context is shared across the test session: drop any kafka items
    # leaked by earlier tests so the fallback paths are exercised cleanly.
    core.discard_item("kafka_cluster_id")
    core.set_item("kafka_topic", topic)
    if cluster_id is not ...:
        core.set_item("kafka_cluster_id", cluster_id)
    try:
        core.dispatch("kafka.produce.start", (producer, (), {}, False, None))
    finally:
        core.discard_item("kafka_topic")
        if cluster_id is not ...:
            core.discard_item("kafka_cluster_id")


def _dispatch_consume(consumer, message):
    core.dispatch("kafka.consume.start", (consumer, message, None))


def test_produce_records_side_with_cluster_id(fresh_reporter):
    # T-5: produce side values (direction 0, group "") and a source link.
    _dispatch_produce(FakeProducer(), "orders", cluster_id="cluster-1")

    report = fresh_reporter.take_report("svc", "env")
    assert report["sides"] == [["produce", "kafka", "cluster-1", "orders", ""]]
    assert report["links"] == [[-1, 0]]
    assert report["service"] == "svc"
    assert report["env"] == "env"


def test_produce_without_cluster_id_records_empty_cluster(fresh_reporter):
    # T-5: no kafka_cluster_id item -> cluster "" (never raw bootstrap servers,
    # which vary per client config; the backend matches clusterless sides on
    # (queue_type, topic)). The second produce interns to the same single side.
    producer = FakeProducer(bootstrap_servers="host1:9092")
    _dispatch_produce(producer, "orders")
    _dispatch_produce(producer, "orders")

    report = fresh_reporter.take_report("svc", "env")
    assert report["sides"] == [["produce", "kafka", "", "orders", ""]]


def test_consume_records_side(fresh_reporter):
    # T-5: consume side values (direction consume, group per config); cluster is
    # the resolved cluster id, "" when none (never bootstrap servers).
    consumer = FakeConsumer(bootstrap_servers="host1:9092", group_id="g1")
    _dispatch_consume(consumer, FakeMessage("orders"))

    report = fresh_reporter.take_report("svc", "env")
    assert report["sides"] == [["consume", "kafka", "", "orders", "g1"]]


def test_consume_uses_resolved_cluster_id(fresh_reporter):
    # T-5: the consumer's cluster id (shared bootstrap cache) is used when known.
    consumer = FakeConsumer(bootstrap_servers="host1:9092", cluster_id="cluster-1", group_id="g1")
    _dispatch_consume(consumer, FakeMessage("orders"))

    report = fresh_reporter.take_report("svc", "env")
    assert report["sides"] == [["consume", "kafka", "cluster-1", "orders", "g1"]]


def test_consume_group_empty_when_unset(fresh_reporter):
    # T-5: manual assign without group.id -> group "".
    consumer = FakeConsumer(bootstrap_servers="host1:9092", group_id="")
    _dispatch_consume(consumer, FakeMessage("orders"))

    report = fresh_reporter.take_report("svc", "env")
    assert report["sides"] == [["consume", "kafka", "", "orders", ""]]


def test_consume_error_message_records_no_side(fresh_reporter):
    # T-5: a transport-error event is not a consumed message (mirrors DSM).
    consumer = FakeConsumer(cluster_id="cluster-1", group_id="g1")
    _dispatch_consume(consumer, FakeMessage("orders", error=object()))

    assert fresh_reporter.take_report("svc", "env") is None


def test_produce_links_to_current_consume(fresh_reporter):
    # T-3: produce inside the consume context on the same thread links to the
    # consumed side instead of recording a source.
    consumer = FakeConsumer(cluster_id="cluster-1", group_id="g1")
    _dispatch_consume(consumer, FakeMessage("in-topic"))
    _dispatch_produce(FakeProducer(), "out-topic", cluster_id="cluster-1")

    report = fresh_reporter.take_report("svc", "env")
    consume_idx = report["sides"].index(["consume", "kafka", "cluster-1", "in-topic", "g1"])
    produce_idx = report["sides"].index(["produce", "kafka", "cluster-1", "out-topic", ""])
    assert report["links"] == [[consume_idx, produce_idx]]


def test_consume_batch_records_each_message_topic_and_last_wins(fresh_reporter):
    # T-5/T-4: kafka.consume.start fires per polled message; each message's own
    # topic is recorded and the last consumed message replaces the context.
    consumer = FakeConsumer(cluster_id="cluster-1", group_id="g1")
    _dispatch_consume(consumer, FakeMessage("topic-a"))
    _dispatch_consume(consumer, FakeMessage("topic-b"))
    _dispatch_produce(FakeProducer(), "out-topic", cluster_id="cluster-1")

    report = fresh_reporter.take_report("svc", "env")
    assert ["consume", "kafka", "cluster-1", "topic-a", "g1"] in report["sides"]
    assert ["consume", "kafka", "cluster-1", "topic-b", "g1"] in report["sides"]
    consume_idx = report["sides"].index(["consume", "kafka", "cluster-1", "topic-b", "g1"])
    produce_idx = report["sides"].index(["produce", "kafka", "cluster-1", "out-topic", ""])
    # topic-a's consume was replaced by topic-b's: the produce links to topic-b.
    assert report["links"] == [[consume_idx, produce_idx]]


def test_produce_on_other_thread_is_source(fresh_reporter):
    # T-3/T-4: the consume context is per-thread, so a produce on another
    # thread records a source link even while the main thread holds one.
    consumer = FakeConsumer(cluster_id="cluster-1", group_id="g1")
    _dispatch_consume(consumer, FakeMessage("in-topic"))

    def produce_on_other_thread():
        core.set_item("kafka_topic", "out-topic")
        core.set_item("kafka_cluster_id", "cluster-1")
        try:
            pipelines_kafka_message_produce(FakeProducer(), (), {}, False, None)
        finally:
            core.discard_item("kafka_topic")
            core.discard_item("kafka_cluster_id")

    thread = threading.Thread(target=produce_on_other_thread)
    thread.start()
    thread.join()

    report = fresh_reporter.take_report("svc", "env")
    produce_idx = report["sides"].index(["produce", "kafka", "cluster-1", "out-topic", ""])
    assert report["links"] == [[-1, produce_idx]]


def test_produce_after_consumed_context_exited_is_source(fresh_reporter):
    # T-4: producing after the consumed context was explicitly exited via the
    # context manager records a source link.
    with fresh_reporter.on_consume("kafka", "cluster-1", "in-topic", "g1"):
        pass
    _dispatch_produce(FakeProducer(), "out-topic", cluster_id="cluster-1")

    report = fresh_reporter.take_report("svc", "env")
    assert ["consume", "kafka", "cluster-1", "in-topic", "g1"] in report["sides"]
    produce_idx = report["sides"].index(["produce", "kafka", "cluster-1", "out-topic", ""])
    assert report["links"] == [[-1, produce_idx]]


def test_handlers_noop_when_pipelines_disabled(monkeypatch):
    # T-5: while DD_DATA_STREAMS_PIPELINES_ENABLED is off the handlers are a
    # no-op: get_pipelines_reporter() is None and nothing is recorded.
    monkeypatch.setattr(pipelines_module, "_pipelines_enabled", lambda: False)
    disabled_reporter = pipelines_module.PipelinesReporter()
    pipelines_module._reporter = disabled_reporter
    consumer = FakeConsumer(cluster_id="cluster-1", group_id="g1")

    _dispatch_consume(consumer, FakeMessage("orders"))
    _dispatch_produce(FakeProducer(), "orders", cluster_id="cluster-1")

    assert get_pipelines_reporter() is None
    assert not disabled_reporter._sides
    assert not disabled_reporter._links


def test_handlers_never_raise(fresh_reporter):
    # T-13: handlers must never raise into application code.
    consumer = FakeConsumer(cluster_id="cluster-1", group_id="g1")
    _dispatch_consume(consumer, BrokenMessage())
    _dispatch_consume(None, FakeMessage("orders"))
    _dispatch_produce(FakeProducer(), "orders", cluster_id="cluster-1")

    report = fresh_reporter.take_report("svc", "env")
    assert ["produce", "kafka", "cluster-1", "orders", ""] in report["sides"]
