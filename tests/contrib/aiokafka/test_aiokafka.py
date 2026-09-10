import asyncio
from types import SimpleNamespace

from aiokafka.errors import MessageSizeTooLargeError
from aiokafka.structs import TopicPartition
import pytest

from ddtrace._trace.context import Context
from ddtrace.contrib.internal.aiokafka.patch import patch
from ddtrace.contrib.internal.aiokafka.patch import traced_getmany
from ddtrace.contrib.internal.aiokafka.patch import traced_getone
from ddtrace.contrib.internal.aiokafka.patch import traced_send
from ddtrace.contrib.internal.aiokafka.patch import unpatch
from ddtrace.propagation.http import HTTPPropagator
from tests.utils import override_config
from tests.utils import override_global_tracer

from .utils import BOOTSTRAP_SERVERS
from .utils import KEY
from .utils import PAYLOAD
from .utils import consumer_ctx
from .utils import create_topic
from .utils import find_span_by_name
from .utils import has_header
from .utils import producer_ctx


@pytest.fixture(autouse=True)
def patch_aiokafka():
    """Automatically patch aiokafka for all tests in this class"""
    patch()
    yield
    unpatch()


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [KEY, None])
@pytest.mark.snapshot()
async def test_send_and_wait_key(key):
    topic = await create_topic("send_and_wait_key")
    async with producer_ctx([BOOTSTRAP_SERVERS]) as producer:
        await producer.send_and_wait(topic, value=PAYLOAD, key=key)

    async with consumer_ctx([topic]) as consumer:
        await consumer.getone()
        await consumer.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [PAYLOAD, None])
@pytest.mark.snapshot()
async def test_send_and_wait_value(value):
    topic = await create_topic("send_and_wait_value")
    async with producer_ctx([BOOTSTRAP_SERVERS]) as producer:
        await producer.send_and_wait(topic, value=value, key=KEY)

    async with consumer_ctx([topic]) as consumer:
        await consumer.getone()
        await consumer.commit()


@pytest.mark.asyncio
@pytest.mark.snapshot()
async def test_send_and_wait_commit_with_offset():
    topic = await create_topic("send_and_wait_commit_with_offset")
    async with producer_ctx([BOOTSTRAP_SERVERS]) as producer:
        await producer.send_and_wait(topic, value=PAYLOAD, key=KEY)

    async with consumer_ctx([topic]) as consumer:
        result = await consumer.getone()
        await consumer.commit({TopicPartition(result.topic, result.partition): result.offset + 1})

        assert not has_header(result.headers, "x-datadog-trace-id")


@pytest.mark.asyncio
@pytest.mark.snapshot()
async def test_send_commit():
    topic = await create_topic("send_commit")
    async with producer_ctx([BOOTSTRAP_SERVERS]) as producer:
        fut = await producer.send(topic, value=PAYLOAD, key=KEY)
        await fut

    async with consumer_ctx([topic]) as consumer:
        result = await consumer.getone()
        await consumer.commit()

        assert not has_header(result.headers, "x-datadog-trace-id")


@pytest.mark.asyncio
async def test_send_span_finishes_when_delivery_future_resolves(tracer, test_spans):
    delivery_future = asyncio.get_running_loop().create_future()
    client = SimpleNamespace(_bootstrap_servers=[BOOTSTRAP_SERVERS], _dd_cluster_id="test-cluster")
    producer = SimpleNamespace(client=client)

    async def send(*args, **kwargs):
        return delivery_future

    returned_future = await traced_send(send, producer, ("topic", PAYLOAD), {})
    span = tracer.current_span()

    assert returned_future is delivery_future
    assert span is not None
    assert not span.finished
    test_spans.assert_has_no_spans()

    delivery_future.set_result(SimpleNamespace(topic="topic", partition=0, offset=1))
    await asyncio.sleep(0)

    assert span.finished
    test_spans.assert_span_count(1)


@pytest.mark.asyncio
async def test_send_span_records_delivery_future_failure(tracer, test_spans):
    delivery_future = asyncio.get_running_loop().create_future()
    client = SimpleNamespace(_bootstrap_servers=[BOOTSTRAP_SERVERS], _dd_cluster_id="test-cluster")
    producer = SimpleNamespace(client=client)

    async def send(*args, **kwargs):
        return delivery_future

    await traced_send(send, producer, ("topic", PAYLOAD), {})
    span = tracer.current_span()

    assert span is not None
    assert not span.finished

    delivery_future.set_exception(RuntimeError("delivery failed"))
    await asyncio.sleep(0)

    assert span.finished
    assert span.error == 1
    test_spans.assert_span_count(1)


@pytest.mark.asyncio
async def test_getone_preserves_local_span_when_headers_are_from_another_trace(tracer, test_spans):
    carrier = {}
    HTTPPropagator.inject(Context(trace_id=2**64 - 1, span_id=99), carrier)
    message = SimpleNamespace(
        headers=[(key, value.encode("utf-8") if isinstance(value, str) else value) for key, value in carrier.items()],
        topic="topic",
        key=None,
        value=PAYLOAD,
        partition=0,
        offset=1,
    )
    client = SimpleNamespace(_bootstrap_servers=[BOOTSTRAP_SERVERS], _dd_cluster_id="test-cluster")
    consumer = SimpleNamespace(_client=client, _group_id="test-group")

    async def getone(*args, **kwargs):
        return message

    with override_config("aiokafka", dict(distributed_tracing_enabled=True)):
        with tracer.trace("local") as parent:
            result = await traced_getone(getone, consumer, (), {})
            assert result is message
            assert tracer.current_span() is parent
            with tracer.trace("after") as after:
                assert after.parent_id == parent.span_id


@pytest.mark.asyncio
async def test_getmany_empty_result():
    client = SimpleNamespace(_bootstrap_servers=[BOOTSTRAP_SERVERS], _dd_cluster_id="test-cluster")
    consumer = SimpleNamespace(_client=client, _group_id="test-group")

    async def getmany(*args, **kwargs):
        return {}

    result = await traced_getmany(getmany, consumer, (), {})

    assert result == {}


@pytest.mark.asyncio
async def test_getmany_preserves_local_span(tracer, test_spans):
    client = SimpleNamespace(_bootstrap_servers=[BOOTSTRAP_SERVERS], _dd_cluster_id="test-cluster")
    consumer = SimpleNamespace(_client=client, _group_id="test-group")
    active_span_during_getmany = None

    async def getmany(*args, **kwargs):
        nonlocal active_span_during_getmany
        active_span_during_getmany = tracer.current_span()
        return {}

    with tracer.trace("local") as parent:
        result = await traced_getmany(getmany, consumer, (), {})

        assert result == {}
        assert active_span_during_getmany is parent
        assert tracer.current_span() is parent
        test_spans.assert_span_count(1)
        consume_span = test_spans.pop()[0]
        assert consume_span.parent_id is None


@pytest.mark.asyncio
@pytest.mark.snapshot()
async def test_send_multiple_servers():
    topic = await create_topic("send_multiple_servers")
    async with producer_ctx([BOOTSTRAP_SERVERS] * 3) as producer:
        await producer.send_and_wait(topic, value=PAYLOAD, key=KEY)
        await producer.flush()


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=["meta.error.stack", "meta.error.message"])
async def test_send_and_wait_failure(test_spans):
    async with producer_ctx([BOOTSTRAP_SERVERS]) as producer:
        with pytest.raises(MessageSizeTooLargeError):
            await producer.send_and_wait("nonexistent_topic", value=b"x" * (10 * 1024 * 1024), key=KEY)
            spans = test_spans.pop()
            assert spans[0].meta["error.message"].startswith("[Error 10] MessageSizeTooLargeError: ")


@pytest.mark.asyncio
@pytest.mark.snapshot()
async def test_getmany_single_message():
    topic = await create_topic("getmany_single_message")
    async with producer_ctx([BOOTSTRAP_SERVERS]) as producer:
        await producer.send_and_wait(topic, value=PAYLOAD, key=KEY)

    async with consumer_ctx([topic]) as consumer:
        await consumer.getmany(timeout_ms=1000)
        await consumer.commit()


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=["meta.messaging.destination.name", "meta.kafka.topic"])
async def test_getmany_multiple_messages_multiple_topics():
    topic = await create_topic("getmany_multiple_messages_multiple_topics")
    topic_2 = await create_topic("getmany_multiple_messages_multiple_topics_2")

    async with producer_ctx([BOOTSTRAP_SERVERS] * 3) as producer:
        await producer.send_and_wait(topic, PAYLOAD)
        await producer.send_and_wait(topic, PAYLOAD)
        await producer.send_and_wait(topic_2, PAYLOAD)

    async with consumer_ctx([topic, topic_2]) as consumer:
        await consumer.getmany(timeout_ms=1000)
        await consumer.commit()


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=["meta.tracestate"])
async def test_send_and_wait_with_distributed_tracing():
    topic = await create_topic("send_and_wait_with_distributed_tracing")

    with override_config("aiokafka", dict(distributed_tracing_enabled=True)):
        async with producer_ctx([BOOTSTRAP_SERVERS]) as producer:
            await producer.send_and_wait(
                topic, value=PAYLOAD, key=KEY, headers=[("some_header", "some_value".encode("utf-8"))]
            )

        async with consumer_ctx([topic]) as consumer:
            result = await consumer.getone()
            await consumer.commit()

        assert has_header(result.headers, "x-datadog-trace-id")
        assert has_header(result.headers, "some_header")


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=["meta._dd.span_links", "meta.messaging.destination.name", "meta.kafka.topic"])
async def test_getmany_multiple_messages_multiple_topics_with_distributed_tracing(tracer, test_spans):
    topic = await create_topic("getmany_distributed_tracing")
    topic_2 = await create_topic("getmany_distributed_tracing_2")

    with override_global_tracer(tracer):
        with override_config("aiokafka", dict(distributed_tracing_enabled=True)):
            async with producer_ctx([BOOTSTRAP_SERVERS] * 3) as producer:
                await producer.send_and_wait(topic, PAYLOAD)
                await producer.send_and_wait(topic, PAYLOAD)
                await producer.send_and_wait(topic_2, PAYLOAD)

            async with consumer_ctx([topic, topic_2]) as consumer:
                result = await consumer.getmany(timeout_ms=1000)
                await consumer.commit()

                headers = next(iter(result.values()))[0].headers
                assert has_header(headers, "x-datadog-trace-id")

    spans = test_spans.pop()
    consumer_span = find_span_by_name(spans, "kafka.consume")

    assert consumer_span is not None, "Consumer span not found"

    span_links = consumer_span._get_links()
    assert span_links is not None, "Consumer span should have span links"
    assert len(span_links) == 3, "Consumer span should have at least one span link"
