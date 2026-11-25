import pytest


DSM_ENV = {"DD_DATA_STREAMS_ENABLED": "true", "_DD_TRACE_STATS_WRITER_INTERVAL": "1000"}


@pytest.mark.subprocess(env=DSM_ENV, err=None)
def test_redis_task_produce_and_consume_checkpoints_are_linked():
    from tests.contrib.celery.base import BROKER_URL
    from tests.contrib.celery.dsm_helpers import assert_linked_edge
    from tests.contrib.celery.dsm_helpers import run_one_task

    headers = run_one_task(BROKER_URL)

    assert "dd-pathway-ctx-base64" in headers
    assert_linked_edge("redis")


@pytest.mark.subprocess(env=DSM_ENV, err=None)
def test_amqp_task_produce_and_consume_checkpoints_are_linked():
    from tests.contrib.celery.base import AMQP_BROKER_URL
    from tests.contrib.celery.dsm_helpers import assert_linked_edge
    from tests.contrib.celery.dsm_helpers import run_one_task

    headers = run_one_task(AMQP_BROKER_URL)

    assert "dd-pathway-ctx-base64" in headers
    assert_linked_edge("rabbitmq")


@pytest.mark.subprocess(env=DSM_ENV, err=None)
def test_amqp_task_with_kombu_patched_sets_one_produce_checkpoint():
    from tests.contrib.celery.base import AMQP_BROKER_URL
    from tests.contrib.celery.dsm_helpers import assert_linked_edge
    from tests.contrib.celery.dsm_helpers import checkpoints
    from tests.contrib.celery.dsm_helpers import run_one_task

    run_one_task(AMQP_BROKER_URL, with_kombu=True)

    assert_linked_edge("rabbitmq")
    for tags, _, _ in checkpoints("out"):
        assert "exchange:celeryev" not in tags
        assert "pidbox" not in tags


@pytest.mark.subprocess(env=DSM_ENV, err=None)
def test_redis_task_with_kombu_patched_sets_one_produce_checkpoint():
    from tests.contrib.celery.base import BROKER_URL
    from tests.contrib.celery.dsm_helpers import assert_linked_edge
    from tests.contrib.celery.dsm_helpers import run_one_task

    run_one_task(BROKER_URL, with_kombu=True)

    assert_linked_edge("redis")


@pytest.mark.subprocess(env={"DD_DATA_STREAMS_ENABLED": "false"}, err=None)
def test_no_checkpoints_without_data_streams():
    from ddtrace.internal.datastreams import data_streams_processor
    from tests.contrib.celery.base import BROKER_URL
    from tests.contrib.celery.dsm_helpers import run_one_task

    headers = run_one_task(BROKER_URL)

    assert "dd-pathway-ctx-base64" not in headers
    assert data_streams_processor() is None


@pytest.mark.subprocess(env=DSM_ENV, err=None)
def test_kombu_sets_no_checkpoints_on_celery_internal_exchanges():
    from unittest import mock

    from ddtrace.contrib.internal.celery.patch import patch
    from ddtrace.internal.datastreams import data_streams_processor
    from ddtrace.internal.datastreams.kombu import handle_kombu_consume
    from ddtrace.internal.datastreams.kombu import handle_kombu_produce

    patch()

    for exchange in ("celeryev", "celery.pidbox", "reply.celery.pidbox"):
        publish_args = [b"{}", 0, "application/json", "utf-8", {}, {}, "", False, False, exchange]
        handle_kombu_produce(publish_args, {}, None)
        message = mock.Mock(delivery_info={"exchange": exchange, "routing_key": ""}, headers={}, body=b"{}")
        handle_kombu_consume(mock.Mock(queues=[]), message, None)

    assert not data_streams_processor()._buckets


@pytest.mark.subprocess(env=DSM_ENV, err=None)
def test_kombu_keeps_pidbox_checkpoints_without_celery_instrumentation():
    from ddtrace.internal.datastreams import data_streams_processor
    from ddtrace.internal.datastreams.kombu import handle_kombu_produce

    publish_args = [b"{}", 0, "application/json", "utf-8", {}, {}, "", False, False, "notifications.pidbox"]
    handle_kombu_produce(publish_args, {}, None)

    assert data_streams_processor()._buckets


def test_celery_internal_exchanges_are_skipped_by_kombu():
    from ddtrace.internal.datastreams.kombu import is_celery_internal_exchange

    assert is_celery_internal_exchange("celeryev")
    assert is_celery_internal_exchange("celery.pidbox")
    assert is_celery_internal_exchange("reply.celery.pidbox")
    assert not is_celery_internal_exchange("")
    assert not is_celery_internal_exchange("celery")
    assert not is_celery_internal_exchange(None)


@pytest.mark.parametrize(
    "conf,expected",
    [
        ({"broker_url": "amqp://guest@localhost//"}, "rabbitmq"),
        ({"broker_url": "pyamqp://guest@localhost//"}, "rabbitmq"),
        ({"broker_url": "redis://localhost:6379/0"}, "redis"),
        ({"broker_url": "rediss://localhost:6379/0"}, "redis"),
        ({"broker_url": "redis+socket:///tmp/redis.sock"}, "redis"),
        ({"broker_url": ["redis://a:6379/0", "redis://b:6379/0"]}, "redis"),
        ({"broker_url": "sentinel://s1:26379;sentinel://s2:26379"}, "redis"),
        ({"broker_url": "redis-host", "broker_transport": "redis"}, "redis"),
        ({"broker_url": "rabbit-host"}, "rabbitmq"),
        ({"broker_url": "sqs://"}, None),
        ({"broker_url": "memory://"}, None),
        ({"broker_url": None}, "rabbitmq"),
        ({"broker_url": None, "broker_transport": "redis"}, "redis"),
        ({"broker_url": "amqp://a//", "broker_write_url": "redis://b:6379/0"}, "redis"),
        ({"broker_url": None, "broker_transport": object()}, None),
    ],
)
def test_broker_type(conf, expected, monkeypatch):
    from celery import Celery

    for name in ("CELERY_BROKER_URL", "CELERY_BROKER_READ_URL", "CELERY_BROKER_WRITE_URL"):
        monkeypatch.delenv(name, raising=False)

    from ddtrace.internal.datastreams.celery import _broker_type

    app = Celery("dsm_broker_type")
    app.conf.update(conf)

    assert _broker_type(app, "broker_write_url") == expected


@pytest.mark.parametrize(
    "env,side,expected",
    [
        ({"CELERY_BROKER_URL": "redis://env:6379/0"}, "broker_write_url", "redis"),
        ({"CELERY_BROKER_URL": "redis://env:6379/0"}, "broker_read_url", "redis"),
        ({"CELERY_BROKER_WRITE_URL": "redis://env:6379/0"}, "broker_write_url", "redis"),
        ({"CELERY_BROKER_WRITE_URL": "redis://env:6379/0"}, "broker_read_url", "rabbitmq"),
        ({"CELERY_BROKER_READ_URL": "redis://env:6379/0"}, "broker_read_url", "redis"),
        ({"CELERY_BROKER_READ_URL": "redis://env:6379/0"}, "broker_write_url", "rabbitmq"),
    ],
)
def test_broker_type_reads_celery_broker_url_environment_variables(env, side, expected, monkeypatch):
    from celery import Celery

    from ddtrace.internal.datastreams.celery import _broker_type

    for name in ("CELERY_BROKER_URL", "CELERY_BROKER_READ_URL", "CELERY_BROKER_WRITE_URL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    app = Celery("dsm_broker_env", broker="amqp://guest@localhost//")

    assert _broker_type(app, side) == expected


def test_body_size_counts_positional_and_keyword_arguments():
    from ddtrace.internal.datastreams.celery import _body_size

    argument = "x" * 1024
    protocol_2 = ((argument, b"abc"), {"key": "value"}, {"callbacks": None})
    protocol_1 = {"task": "add", "id": "1", "args": [argument, b"abc"], "kwargs": {"key": "value"}}

    assert _body_size(protocol_2) == 1024 + 3 + 3 + 5
    assert _body_size(protocol_1) == 1024 + 3 + 3 + 5
    assert _body_size(b"raw") == 3


@pytest.mark.subprocess(env=DSM_ENV, err=None)
def test_payload_size_counts_positional_task_arguments():
    from ddtrace.internal.datastreams.processor import DataStreamsCtx
    from tests.contrib.celery.base import BROKER_URL
    from tests.contrib.celery.dsm_helpers import run_one_task

    recorded = []
    original = DataStreamsCtx.set_checkpoint

    def set_checkpoint(self, tags, *args, **kwargs):
        recorded.append((tags[0], kwargs.get("payload_size", 0)))
        return original(self, tags, *args, **kwargs)

    DataStreamsCtx.set_checkpoint = set_checkpoint
    argument_size = 100_000
    run_one_task(BROKER_URL, args=("x" * argument_size, "y"))

    for direction in ("direction:out", "direction:in"):
        sizes = [size for tag, size in recorded if tag == direction]
        assert len(sizes) == 1, recorded
        assert sizes[0] > argument_size, recorded


def test_edge_tags():
    from ddtrace.internal.datastreams.celery import _edge_tags

    assert _edge_tags("out", "", "tasks", "redis") == ["direction:out", "topic:tasks", "type:redis"]
    assert _edge_tags("in", "media", "media.image", "rabbitmq") == [
        "direction:in",
        "topic:media.image",
        "type:rabbitmq",
    ]
    assert _edge_tags("out", "broadcast", None, "rabbitmq") == [
        "direction:out",
        "exchange:broadcast",
        "type:rabbitmq",
    ]
    assert _edge_tags("in", None, None, "redis") == ["direction:in", "type:redis"]


def _assert_routed_edge(broker_type):
    from tests.contrib.celery.dsm_helpers import ROUTING_KEY
    from tests.contrib.celery.dsm_helpers import checkpoints
    from tests.contrib.celery.dsm_helpers import pathway_stats

    expected_tags = f"topic:{ROUTING_KEY},type:{broker_type}"
    outs = checkpoints("out")
    ins = checkpoints("in")
    assert [c[0] for c in outs] == [f"direction:out,{expected_tags}"], pathway_stats()
    assert [c[0] for c in ins] == [f"direction:in,{expected_tags}"], pathway_stats()
    assert ins[0][2] == outs[0][1], pathway_stats()


@pytest.mark.subprocess(env=DSM_ENV, err=None)
def test_redis_task_sent_by_name_with_routing_key_has_same_topic_on_both_sides():
    from tests.contrib.celery.base import BROKER_URL
    from tests.contrib.celery.dsm_helpers import run_task_sent_by_name_with_routing_key
    from tests.contrib.celery.test_data_streams import _assert_routed_edge

    run_task_sent_by_name_with_routing_key(BROKER_URL)

    _assert_routed_edge("redis")


@pytest.mark.subprocess(env=DSM_ENV, err=None)
def test_amqp_task_sent_by_name_with_routing_key_has_same_topic_on_both_sides():
    from tests.contrib.celery.base import AMQP_BROKER_URL
    from tests.contrib.celery.dsm_helpers import run_task_sent_by_name_with_routing_key
    from tests.contrib.celery.test_data_streams import _assert_routed_edge

    run_task_sent_by_name_with_routing_key(AMQP_BROKER_URL)

    _assert_routed_edge("rabbitmq")


@pytest.mark.subprocess(env=DSM_ENV, err=None)
def test_redis_protocol_1_task_checkpoints_are_linked():
    from tests.contrib.celery.base import BROKER_URL
    from tests.contrib.celery.dsm_helpers import assert_linked_edge
    from tests.contrib.celery.dsm_helpers import run_one_task

    headers = run_one_task(BROKER_URL, task_protocol=1)

    assert "dd-pathway-ctx-base64" in headers
    assert_linked_edge("redis")


@pytest.mark.subprocess(env=DSM_ENV, err=None)
def test_amqp_protocol_1_task_checkpoints_are_linked():
    from tests.contrib.celery.base import AMQP_BROKER_URL
    from tests.contrib.celery.dsm_helpers import assert_linked_edge
    from tests.contrib.celery.dsm_helpers import run_one_task

    headers = run_one_task(AMQP_BROKER_URL, task_protocol=1)

    assert "dd-pathway-ctx-base64" in headers
    assert_linked_edge("rabbitmq")


@pytest.mark.subprocess(env=DSM_ENV, err=None)
def test_kombu_skips_celery_task_messages_already_checkpointed_by_celery():
    from ddtrace.contrib.internal.celery.patch import patch
    from ddtrace.internal.datastreams import data_streams_processor
    from ddtrace.internal.datastreams.kombu import handle_kombu_produce

    def publish(headers):
        handle_kombu_produce([b"{}", 0, "application/json", "utf-8", headers, {}, "tasks", False, False, ""], {}, None)

    publish({"task": "dsm_celery.add", "dd-pathway-ctx-base64": "AAAAAAAAAAA="})
    assert data_streams_processor()._buckets

    data_streams_processor()._buckets.clear()
    patch()
    publish({"task": "dsm_celery.add", "dd-pathway-ctx-base64": "AAAAAAAAAAA="})
    assert not data_streams_processor()._buckets

    publish({"task": "dsm_celery.add"})
    publish({"dd-pathway-ctx-base64": "AAAAAAAAAAA="})
    stats = [s for bucket in data_streams_processor()._buckets.values() for s in bucket.pathway_stats.values()]
    assert sum(stat.full_pathway_latency.count for stat in stats) == 2
