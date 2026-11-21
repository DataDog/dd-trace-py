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

    from ddtrace.internal.datastreams import data_streams_processor
    from ddtrace.internal.datastreams.kombu import handle_kombu_consume
    from ddtrace.internal.datastreams.kombu import handle_kombu_produce

    for exchange in ("celeryev", "celery.pidbox", "reply.celery.pidbox"):
        publish_args = [b"{}", 0, "application/json", "utf-8", {}, {}, "", False, False, exchange]
        handle_kombu_produce(publish_args, {}, None)
        message = mock.Mock(delivery_info={"exchange": exchange, "routing_key": ""}, headers={}, body=b"{}")
        handle_kombu_consume(mock.Mock(queues=[]), message, None)

    assert not data_streams_processor()._buckets


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

    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)

    from ddtrace.internal.datastreams.celery import _broker_type

    app = Celery("dsm_broker_type")
    app.conf.update(conf)

    assert _broker_type(app, "broker_write_url") == expected


def test_queue_name_resolution():
    from celery import Celery
    from kombu import Exchange
    from kombu import Queue

    from ddtrace.internal.datastreams.celery import _edge_tags

    app = Celery("dsm_queue_name")
    app.conf.task_queues = [Queue("images", Exchange("media"), routing_key="media.image")]

    assert _edge_tags("out", app, "", "tasks", "redis") == ["direction:out", "topic:tasks", "type:redis"]
    assert _edge_tags("in", app, "media", "media.image", "rabbitmq") == [
        "direction:in",
        "topic:images",
        "type:rabbitmq",
    ]
    assert _edge_tags("out", app, "media", "media.video", "rabbitmq") == [
        "direction:out",
        "exchange:media",
        "has_routing_key:true",
        "type:rabbitmq",
    ]
