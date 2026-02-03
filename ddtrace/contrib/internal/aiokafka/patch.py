import os
from time import time_ns
from typing import Dict
from typing import List

import aiokafka
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.contrib import trace_utils
from ddtrace.contrib.events.aiokafka import AioKafkaProduceCompleted
from ddtrace.contrib.events.kafka import KafkaMessagingProduceEvent
from ddtrace.contrib.events.kafka import KafkaMessagingConsumeEvent
from ddtrace.ext.kafka import CONSUME
from ddtrace.ext.kafka import PRODUCE
from ddtrace.ext.kafka import SERVICE
from ddtrace.internal import core
from ddtrace.internal.compat import ensure_text
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import set_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.propagation.http import HTTPPropagator


config._add(
    "aiokafka",
    dict(
        _default_service=schematize_service_name("kafka"),
        distributed_tracing_enabled=asbool(os.getenv("DD_KAFKA_PROPAGATION_ENABLED", default=False)),
    ),
)


def get_version() -> str:
    return getattr(aiokafka, "__version__", "")


def _supported_versions() -> Dict[str, str]:
    return {"aiokafka": ">=0.9.0"}

async def traced_send(func, instance, args, kwargs):
    topic = get_argument_value(args, kwargs, 0, "topic")
    value = get_argument_value(args, kwargs, 1, "value", True)
    message_key = get_argument_value(args, kwargs, 2, "key", True) or None
    partition = get_argument_value(args, kwargs, 3, "partition", True)
    headers = get_argument_value(args, kwargs, 5, "headers", True) or []
    bootstrap_servers = instance.client._bootstrap_servers

    event = KafkaMessagingProduceEvent(
            config=config.aiokafka,
            operation=PRODUCE,
            provider="kafka",
            topic=topic,
            bootstrap_servers=bootstrap_servers,
            messaging_system=SERVICE,
            message_key=message_key,
            tombstone=str(value is None),
            value=value,
            partition=kwargs.get("partition", -1),
            headers=headers,
        )
    event["_end_span"] = False

    with core.context_with_event(
        event,
        service=trace_utils.ext_service(None, config.aiokafka),
    ) as ctx:
        args, kwargs = set_argument_value(args, kwargs, 5, "headers", headers, override_unset=True)
        core.dispatch("aiokafka.send.start", (topic, value, message_key, headers, ctx, partition))

        try:
            result = await func(*args, **kwargs)
        except BaseException as e:
            core.dispatch_event(AioKafkaProduceCompleted(ctx, (type(e), e, e.__traceback__), None))
            raise e

        def sent_callback(future):
            try:
                result = future.result()
                core.dispatch_event(AioKafkaProduceCompleted(ctx, (None, None, None), result))
            except Exception as e:
                core.dispatch_event(AioKafkaProduceCompleted(ctx, (type(e), e, e.__traceback__), None))

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
    topic = None
    message_key = None
    message_offset = None
    is_tombstone = None
    message_partition = None

    try:
        message = await func(*args, **kwargs)

        if message is not None:
            topic = str(getattr(message, "topic", None))
            message_key = ensure_text(message.key) if message.key else None
            message_offset = message.offset or - 1
            is_tombstone = str(message.value is None)
            message_partition = message_partition or -1

        if config.aiokafka.distributed_tracing_enabled and message.headers:
            dd_headers = {
                key: (val.decode("utf-8", errors="ignore") if isinstance(val, (bytes, bytearray)) else str(val))
                for key, val in message.headers
                if val is not None
            }
            parent_ctx = HTTPPropagator.extract(dd_headers)
    except Exception as e:
        err = e
        raise err
    finally:
        with core.context_with_event(
            KafkaMessagingConsumeEvent(
                config=config.aiokafka,
                operation=CONSUME,
                provider="kafka",
                messaging_system=SERVICE,
                group_id=group_id,
                destination_name=topic,
                topic=topic,
                is_tombstone=is_tombstone,
                message_offset=message_offset,
                message_key=message_key,
                received_message=str(message is not None),
                partition=message_partition,
                start_ns=start_ns,
                error=err
            ),
            service=trace_utils.ext_service(None, config.aiokafka),
            call_trace=False,
            distributed_context=parent_ctx,
        ) as ctx:
            core.dispatch("aiokafka.getone.message", (instance, ctx, start_ns, message, err))
    return message


async def traced_getmany(func, instance, args, kwargs):
    group_id = instance._group_id
    bootstrap_servers = instance._client._bootstrap_servers

    start_ns = time_ns()
    err = None
    messages = None
    parent_ctx = None

    first_topic = None
    all_topics = None
    message_partition = None

    try:
        messages = await func(*args, **kwargs)

        if messages is not None:
            first_topic = next(iter(messages)).topic

            topics_partitions: Dict[str, List[int]] = {}
            for topic_partition in messages.keys():
                topic = topic_partition.topic
                partition = topic_partition.partition
                if topic not in topics_partitions:
                    topics_partitions[topic] = []
                topics_partitions[topic].append(partition)

            print("COUCOU")
            print(topics_partitions)
            all_topics = list[str](topics_partitions.keys())

            for topic, partitions in topics_partitions.items():
                partition_list = ",".join(map(str, sorted(partitions)))

            # for topic_partition, records in messages.items():
            #     for record in records:
            #         if config.aiokafka.distributed_tracing_enabled and record.headers:
            #             dd_headers = {
            #                 key: (val.decode("utf-8", errors="ignore") if isinstance(val, (bytes, bytearray)) else str(val))
            #                 for key, val in record.headers
            #                 if val is not None
            #             }
            #             context = HTTPPropagator.extract(dd_headers)

            #             span.link_span(context)
    except Exception as e:
        err = e
        raise err
    finally:
        with core.context_with_event(
            KafkaMessagingConsumeEvent(
                config=config.aiokafka,
                operation=CONSUME,
                provider="kafka",
                messaging_system=SERVICE,
                group_id=group_id,
                destination_name=first_topic,
                topic=all_topics,
                is_tombstone=None,
                message_offset=None,
                message_key=None,
                received_message=str(messages is not None),
                partition=message_partition,
                start_ns=start_ns,
                error=err
            ),
            service=trace_utils.ext_service(None, config.aiokafka),
            call_trace=False,
        ) as ctx:
            core.dispatch("aiokafka.getmany.message", (instance, ctx, messages))

            return messages


async def traced_commit(func, instance, args, kwargs):
    result = await func(*args, **kwargs)
    core.dispatch("aiokafka.commit.end", (instance, args, kwargs))
    return result


def patch():
    if getattr(aiokafka, "_datadog_patch", False):
        return
    aiokafka._datadog_patch = True

    _w("aiokafka", "AIOKafkaProducer.send", traced_send)
    _w("aiokafka", "AIOKafkaConsumer.getone", traced_getone)
    _w("aiokafka", "AIOKafkaConsumer.getmany", traced_getmany)
    _w("aiokafka.consumer.group_coordinator", "GroupCoordinator.commit_offsets", traced_commit)


def unpatch():
    if not getattr(aiokafka, "_datadog_patch", False):
        return

    aiokafka._datadog_patch = False

    _u(aiokafka.AIOKafkaProducer, "send")
    _u(aiokafka.AIOKafkaConsumer, "getone")
    _u(aiokafka.AIOKafkaConsumer, "getmany")
    _u(aiokafka.consumer.group_coordinator.GroupCoordinator, "commit_offsets")
