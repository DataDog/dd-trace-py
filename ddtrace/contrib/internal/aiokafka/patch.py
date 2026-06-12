from time import monotonic
from time import time_ns

import aiokafka
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib import trace_utils
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import kafka as kafkax
from ddtrace.ext.kafka import CONSUME
from ddtrace.ext.kafka import GROUP_ID
from ddtrace.ext.kafka import HOST_LIST
from ddtrace.ext.kafka import PRODUCE
from ddtrace.ext.kafka import SERVICE
from ddtrace.ext.kafka import TOPIC
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.constants import MESSAGING_DESTINATION_NAME
from ddtrace.internal.constants import MESSAGING_SYSTEM
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_messaging_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.settings import env
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.version import parse_version
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.propagation.http import HTTPPropagator


if parse_version(aiokafka.__version__) < (0, 13, 0):
    from aiokafka.protocol.metadata import MetadataRequest_v5 as _MetadataRequest
else:
    from aiokafka.protocol.metadata import MetadataRequest as _MetadataRequest


log = get_logger(__name__)


config._add(
    "aiokafka",
    dict(
        _default_service=schematize_service_name("kafka"),
        distributed_tracing_enabled=asbool(env.get("DD_KAFKA_PROPAGATION_ENABLED", default=False)),
    ),
)


def get_version() -> str:
    return getattr(aiokafka, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"aiokafka": ">=0.9.0"}


def common_aiokafka_tags(topic, bootstrap_servers):
    return {
        COMPONENT: config.aiokafka.integration_name,
        TOPIC: topic,
        MESSAGING_DESTINATION_NAME: topic,
        MESSAGING_SYSTEM: SERVICE,
        HOST_LIST: bootstrap_servers,
    }


def common_consume_aiokafka_tags(topic, bootstrap_servers, group_id):
    tags = common_aiokafka_tags(topic, bootstrap_servers)
    tags.update(
        {
            SPAN_KIND: SpanKind.CONSUMER,
            GROUP_ID: group_id,
        }
    )
    return tags


async def _get_cluster_id(client, topic):
    """Fetch and cache the Kafka cluster ID from the broker.

    Uses a 5 minute failure cache to avoid repeated slow calls when the broker
    is unreachable.
    """
    if client is None:
        return ""
    cached = getattr(client, "_dd_cluster_id", None)
    if cached:
        return cached

    last_failure = getattr(client, "_dd_cluster_id_failure_time", 0)
    if monotonic() - last_failure < 300:
        return ""

    try:
        node_id = client.get_random_node()
        if node_id is None:
            await client.force_metadata_update()
            node_id = client.get_random_node()
        if node_id is None:
            client._dd_cluster_id_failure_time = monotonic()
            return ""
        request = _MetadataRequest(topics=[topic] if topic else [], allow_auto_topic_creation=False)
        response = await client.send(node_id, request)
        cluster_id = getattr(response, "cluster_id", "") or ""
        if cluster_id:
            client._dd_cluster_id = cluster_id
        return cluster_id
    except Exception:
        client._dd_cluster_id_failure_time = monotonic()
        log.debug("Failed to get Kafka cluster ID, will retry after 5 minutes")
        return ""


def parse_send(instance, args, kwargs):
    topic = get_argument_value(args, kwargs, 0, "topic")
    value = get_argument_value(args, kwargs, 1, "value", True)
    key = get_argument_value(args, kwargs, 2, "key", True) or None
    partition = get_argument_value(args, kwargs, 3, "partition", True)
    headers = get_argument_value(args, kwargs, 5, "headers", True) or []
    servers = instance.client._bootstrap_servers

    return topic, value, headers, partition, key, servers


async def traced_send(func, instance, args, kwargs):
    topic, value, headers, partition, key, bootstrap_servers = parse_send(instance, args, kwargs)
    cluster_id = await _get_cluster_id(instance.client, topic)

    with core.context_with_data(
        "aiokafka.send",
        span_name=schematize_messaging_operation(PRODUCE, provider="kafka", direction=SpanDirection.OUTBOUND),
        span_type=SpanTypes.WORKER,
        service=trace_utils.ext_service(None, config.aiokafka),
        tags=common_aiokafka_tags(topic, bootstrap_servers),
        integration_config=config.aiokafka,
    ) as ctx:
        core.set_item("kafka_cluster_id", cluster_id)
        if cluster_id and ctx.span is not None:
            ctx.span._set_attribute(kafkax.CLUSTER_ID, cluster_id)
        core.dispatch("aiokafka.send.start", (topic, value, key, headers, ctx, partition))
        args, kwargs = set_argument_value(args, kwargs, 5, "headers", headers, override_unset=True)

        try:
            result = await func(*args, **kwargs)
        except BaseException as e:
            core.dispatch("aiokafka.send.completed", (ctx, (type(e), e, e.__traceback__), None))
            raise e

        def sent_callback(future):
            try:
                result = future.result()
                core.dispatch("aiokafka.send.completed", (ctx, (None, None, None), result))
            except Exception as e:
                core.dispatch("aiokafka.send.completed", (ctx, (type(e), e, e.__traceback__), None))

        result.add_done_callback(sent_callback)
        return result


async def traced_getone(func, instance, args, kwargs):
    # we must get start time now since execute before starting a span in order to get distributed context
    # if it exists
    start_ns = time_ns()
    err = None
    message = None
    parent_ctx = None

    group_id = instance._group_id
    bootstrap_servers = instance._client._bootstrap_servers

    try:
        message = await func(*args, **kwargs)
        if config.aiokafka.distributed_tracing_enabled and message.headers:
            dd_headers = {
                key: (val.decode("utf-8", errors="ignore") if isinstance(val, (bytes, bytearray)) else str(val))
                for key, val in message.headers
                if val is not None
            }
            parent_ctx = HTTPPropagator.extract(dd_headers)
    except Exception as e:
        err = e

    topic = getattr(message, "topic", None)
    # Only resolve cluster id on the success path. On error we use the cached value (or "")
    # to avoid adding a broker round-trip — and a potential exception that would mask `err` —
    # to the consumer's failure path.
    if err is None:
        cluster_id = await _get_cluster_id(instance._client, topic)
    else:
        client = instance._client
        cluster_id = getattr(client, "_dd_cluster_id", "") if client is not None else ""

    with core.context_with_data(
        "aiokafka.getone",
        call_trace=False,
        span_name=schematize_messaging_operation(CONSUME, provider="kafka", direction=SpanDirection.INBOUND),
        span_type=SpanTypes.WORKER,
        service=trace_utils.ext_service(None, config.aiokafka),
        distributed_context=parent_ctx,
        tags=common_consume_aiokafka_tags(topic, bootstrap_servers, group_id),
        integration_config=config.aiokafka,
    ) as ctx:
        core.set_item("kafka_cluster_id", cluster_id)
        if cluster_id and ctx.span is not None:
            ctx.span._set_attribute(kafkax.CLUSTER_ID, cluster_id)
        core.dispatch("aiokafka.getone.message", (instance, ctx, start_ns, message, err))

    if err is not None:
        raise err
    return message


async def traced_getmany(func, instance, args, kwargs):
    group_id = instance._group_id
    bootstrap_servers = instance._client._bootstrap_servers

    with core.context_with_data(
        "aiokafka.getmany",
        call_trace=False,
        span_name=schematize_messaging_operation(CONSUME, provider="kafka", direction=SpanDirection.INBOUND),
        span_type=SpanTypes.WORKER,
        service=trace_utils.ext_service(None, config.aiokafka),
        tags=common_consume_aiokafka_tags(None, bootstrap_servers, group_id),
        integration_config=config.aiokafka,
    ) as ctx:
        messages = await func(*args, **kwargs)

        topic = None
        if messages:
            for records in messages.values():
                if records:
                    topic = records[0].topic
                    break
        cluster_id = await _get_cluster_id(instance._client, topic)
        core.set_item("kafka_cluster_id", cluster_id)
        if cluster_id and ctx.span is not None:
            ctx.span._set_attribute(kafkax.CLUSTER_ID, cluster_id)

        core.dispatch("aiokafka.getmany.message", (instance, ctx, messages))

        return messages


async def traced_commit(func, instance, args, kwargs):
    result = await func(*args, **kwargs)
    # Use only the cached cluster id on the commit path — avoid a metadata
    # round-trip in this hot path. The id is normally populated by a prior
    # produce/consume; if it isn't yet, we just skip the tag for this commit.
    client = getattr(instance, "_client", None)
    cluster_id = getattr(client, "_dd_cluster_id", "") if client is not None else ""
    core.set_item("kafka_cluster_id", cluster_id)
    core.dispatch("aiokafka.commit.end", (instance, args, kwargs))
    return result


async def traced_producer_start(func, instance, args, kwargs):
    result = await func(*args, **kwargs)
    # Eagerly resolve the cluster id once the producer is connected, so the first
    # user send() already has it cached and DSM pathway hashes stay stable from
    # the first message instead of flipping after the first metadata response.
    await _get_cluster_id(instance.client, None)
    return result


async def traced_consumer_start(func, instance, args, kwargs):
    result = await func(*args, **kwargs)
    await _get_cluster_id(instance._client, None)
    return result


def patch():
    if getattr(aiokafka, "_datadog_patch", False):
        return
    aiokafka._datadog_patch = True

    _w("aiokafka", "AIOKafkaProducer.start", traced_producer_start)
    _w("aiokafka", "AIOKafkaProducer.send", traced_send)
    _w("aiokafka", "AIOKafkaConsumer.start", traced_consumer_start)
    _w("aiokafka", "AIOKafkaConsumer.getone", traced_getone)
    _w("aiokafka", "AIOKafkaConsumer.getmany", traced_getmany)
    _w("aiokafka.consumer.group_coordinator", "GroupCoordinator.commit_offsets", traced_commit)


def unpatch():
    if not getattr(aiokafka, "_datadog_patch", False):
        return

    aiokafka._datadog_patch = False

    _u(aiokafka.AIOKafkaProducer, "start")
    _u(aiokafka.AIOKafkaProducer, "send")
    _u(aiokafka.AIOKafkaConsumer, "start")
    _u(aiokafka.AIOKafkaConsumer, "getone")
    _u(aiokafka.AIOKafkaConsumer, "getmany")
    _u(aiokafka.consumer.group_coordinator.GroupCoordinator, "commit_offsets")
