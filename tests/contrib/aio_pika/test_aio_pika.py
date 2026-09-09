import asyncio
from types import SimpleNamespace
from unittest import mock

import pytest
from yarl import URL

from ddtrace import config
from ddtrace.contrib.internal.aio_pika.patch import _action_wrapper
from ddtrace.contrib.internal.aio_pika.patch import _traced_callback
from ddtrace.contrib.internal.aio_pika.patch import _traced_get
from ddtrace.contrib.internal.aio_pika.patch import _traced_process_enter
from ddtrace.contrib.internal.aio_pika.patch import _traced_process_exit
from ddtrace.contrib.internal.aio_pika.patch import _traced_publish
from ddtrace.trace import tracer
from tests.utils import override_config


class FakeMessage:
    def __init__(self, headers=None):
        self.headers = headers if headers is not None else {}
        self.message_id = "message-1"
        self.exchange = "events"
        self.routing_key = "orders.created"
        self.redelivered = False
        connection = SimpleNamespace(url=URL("amqp://guest:secret@rabbitmq:5673/my-vhost"))
        self.channel = SimpleNamespace(connection=connection)


@pytest.mark.asyncio
async def test_publish_preserves_arguments_headers_and_result(test_spans):
    message = FakeMessage({"application": "preserved"})
    exchange = SimpleNamespace(name="events", channel=message.channel)
    calls = []

    async def publish(*args, **kwargs):
        calls.append((args, kwargs, dict(message.headers)))
        return "confirmed"

    with override_config("aio_pika", {"distributed_tracing": True}):
        result = await _traced_publish(publish, exchange, (message, "orders.created"), {"mandatory": False})

    assert result == "confirmed"
    assert calls[0][0] == (message, "orders.created")
    assert calls[0][1] == {"mandatory": False}
    assert calls[0][2]["application"] == "preserved"
    assert "x-datadog-trace-id" in calls[0][2]
    span = test_spans.spans[0]
    assert span.name == span.resource == "rabbitmq.publish"
    assert span.get_tag("span.kind") == "producer"
    assert span.get_tag("messaging.destination.name") == "events"
    assert span.get_tag("out.host") == "rabbitmq"
    assert span.get_tag("network.destination.port") == "5673"
    assert span.get_tag("out.vhost") == "my-vhost"
    assert "secret" not in repr(span.get_tags())


@pytest.mark.asyncio
async def test_default_exchange_uses_routing_key_and_propagation_defaults_off(test_spans):
    message = FakeMessage({"application": "preserved"})
    exchange = SimpleNamespace(name="", channel=message.channel)

    async def publish(*args, **kwargs):
        return None

    await _traced_publish(publish, exchange, (), {"message": message, "routing_key": "orders.created"})

    assert message.headers == {"application": "preserved"}
    assert test_spans.spans[0].get_tag("messaging.destination.name") == "orders.created"


@pytest.mark.asyncio
async def test_publish_failure_preserves_exception(test_spans):
    error = RuntimeError("publisher confirmation failed")

    async def publish(*args, **kwargs):
        raise error

    with pytest.raises(RuntimeError) as raised:
        await _traced_publish(publish, SimpleNamespace(name="events", channel=None), (FakeMessage(), "key"), {})

    assert raised.value is error
    assert test_spans.spans[0].error == 1


@pytest.mark.asyncio
async def test_callback_supports_sync_and_async_and_process_deduplication(test_spans):
    message = FakeMessage()
    process_context = SimpleNamespace(message=message)

    async def enter(*args, **kwargs):
        return message

    async def exit_context(*args, **kwargs):
        return None

    async def callback(received):
        assert received is message
        await _traced_process_enter(enter, process_context, (), {})
        await _traced_process_exit(exit_context, process_context, (None, None, None), {})
        return "done"

    assert await _traced_callback(callback)(message) == "done"
    assert await _traced_callback(lambda received: "sync")(FakeMessage()) == "sync"
    assert [span.name for span in test_spans.spans].count("rabbitmq.consume") == 2


@pytest.mark.asyncio
async def test_get_backdates_wait_and_uses_queue_destination(test_spans):
    message = FakeMessage()

    async def get(*args, **kwargs):
        await asyncio.sleep(0.02)
        return message

    before = tracer.current_span()
    result = await _traced_get(get, SimpleNamespace(name="orders"), (), {"timeout": 1})

    assert result is message
    assert tracer.current_span() is before
    span = test_spans.spans[0]
    assert span.name == span.resource == "rabbitmq.get"
    assert span.duration >= 0.02
    assert span.get_tag("messaging.destination.name") == "orders"


@pytest.mark.asyncio
async def test_get_none_and_failure(test_spans):
    async def empty(*args, **kwargs):
        return None

    async def failed(*args, **kwargs):
        raise asyncio.TimeoutError

    assert await _traced_get(empty, SimpleNamespace(name="orders"), (), {}) is None
    with pytest.raises(asyncio.TimeoutError):
        await _traced_get(failed, SimpleNamespace(name="orders"), (), {})

    spans = [span for span in test_spans.spans if span.name == "rabbitmq.get"]
    assert spans[0].error == 0
    assert spans[1].error == 1
    assert all(span.get_tag("rabbitmq.queue") == "orders" for span in spans)


@pytest.mark.asyncio
async def test_explicit_process_includes_nested_action_and_preserves_settlement_arguments(test_spans):
    message = FakeMessage()
    process_context = SimpleNamespace(message=message)
    received = []

    async def enter(*args, **kwargs):
        return message

    async def ack(*args, **kwargs):
        received.append((args, kwargs))

    async def exit_context(*args, **kwargs):
        await _action_wrapper("ack")(ack, message, (), {"multiple": True})

    await _traced_process_enter(enter, process_context, (), {})
    await _traced_process_exit(exit_context, process_context, (None, None, None), {})

    assert received == [((), {"multiple": True})]
    consume = next(span for span in test_spans.spans if span.name == "rabbitmq.consume")
    action = next(span for span in test_spans.spans if span.name == "rabbitmq.ack")
    assert action.parent_id == consume.span_id
    assert action.get_tag("span.kind") == "client"


@pytest.mark.asyncio
async def test_span_link_setting_is_interpreted_only_by_aio_pika_wrapper(test_spans):
    message = FakeMessage(
        {
            "x-datadog-trace-id": "1234",
            "x-datadog-parent-id": "5678",
        }
    )

    with override_config("aio_pika", {"distributed_tracing": True}):
        with mock.patch.object(config, "_propagation_as_span_links", {"aio_pika"}):
            with tracer.trace("ambient") as ambient:
                await _traced_callback(lambda received: None)(message)

    process = next(span for span in test_spans.spans if span.name == "rabbitmq.consume")
    assert process.parent_id == ambient.span_id
    assert [(link.trace_id, link.span_id) for link in process._get_links()] == [(1234, 5678)]


def test_no_sensitive_or_high_cardinality_message_data_in_tags():
    message = FakeMessage({"authorization": "secret-header"})
    message.body = b"secret-body"
    message.consumer_tag = "consumer-123"
    message.delivery_tag = 999

    from ddtrace.contrib.internal.aio_pika.patch import _message_tags

    tags = repr(_message_tags(message))
    assert "secret-header" not in tags
    assert "secret-body" not in tags
    assert "consumer-123" not in tags
    assert "999" not in tags
