from datetime import datetime
from datetime import timezone
import os
from typing import Union
from uuid import uuid4

from azure.servicebus import ServiceBusClient
from azure.servicebus import ServiceBusMessage
from azure.servicebus import ServiceBusReceiveMode
from azure.servicebus.aio import ServiceBusClient as ServiceBusClientAsync
from azure.servicebus.amqp import AmqpAnnotatedMessage
import pytest


CONNECTION_STRING = "Endpoint=sb://localhost;SharedAccessKeyName=RootManageSharedAccessKey;SharedAccessKey=SAS_KEY_VALUE;UseDevelopmentEmulator=true;"
QUEUE_NAME = "queue.1"
TOPIC_NAME = "topic.1"
SUBSCRIPTION_NAME = "subscription.3"
DEFAULT_APPLICATION_PROPERTIES = {"property": "val", b"byteproperty": b"byteval"}
TRACE_CONTEXT_KEYS = [
    "x-datadog-trace-id",
    "x-datadog-parent-id",
    "x-datadog-sampling-priority",
    "x-datadog-tags",
    "traceparent",
    "tracestate",
]


def normalize_properties(message: Union[ServiceBusMessage, AmqpAnnotatedMessage]):
    props = message.application_properties

    if not props:
        props = {}

    return {k.decode() if isinstance(k, bytes) else k: v for k, v in props.items()}


def make_servicebus_messages():
    return [
        ServiceBusMessage(body='{"body":"ServiceBusMessage without properties"}'),
        ServiceBusMessage(
            body='{"body":"ServiceBusMessage with properties and custom message_id"}',
            application_properties=DEFAULT_APPLICATION_PROPERTIES,
            message_id=str(uuid4()),
        ),
    ]


def make_amqp_annotated_messages():
    return [
        AmqpAnnotatedMessage(data_body='{"body":"AmqpAnnotatedMessage without properties"}'),
        AmqpAnnotatedMessage(
            data_body='{"body":"AmqpAnnotatedMessage with properties and custom message_id"}',
            application_properties=DEFAULT_APPLICATION_PROPERTIES,
            properties={"message_id": uuid4()},
        ),
    ]


def run_test(sender, receiver, method, message_payload_type, distributed_tracing_enabled, batch_links_enabled):
    message_length = 0
    now = datetime.now(timezone.utc)

    if method == "send_messages" and message_payload_type == "single":
        for servicebus_message in make_servicebus_messages():
            sender.send_messages(servicebus_message)
            message_length += 1
        for amqp_annotated_message in make_amqp_annotated_messages():
            sender.send_messages(amqp_annotated_message)
            message_length += 1
    if method == "send_messages" and message_payload_type == "list":
        servicebus_messages = make_servicebus_messages()
        sender.send_messages(servicebus_messages)

        amqp_annotated_messages = make_amqp_annotated_messages()
        sender.send_messages(amqp_annotated_messages)

        message_length = len(servicebus_messages) + len(amqp_annotated_messages)
    elif method == "send_messages" and message_payload_type == "batch":
        servicebus_message_batch = sender.create_message_batch()
        for servicebus_message in make_servicebus_messages():
            servicebus_message_batch.add_message(servicebus_message)
            message_length += 1
        sender.send_messages(servicebus_message_batch)

        amqp_annotated_message_batch = sender.create_message_batch()
        for amqp_annotated_message in make_amqp_annotated_messages():
            amqp_annotated_message_batch.add_message(amqp_annotated_message)
            message_length += 1
        sender.send_messages(amqp_annotated_message_batch)
    elif method == "schedule_messages" and message_payload_type == "single":
        for servicebus_message in make_servicebus_messages():
            sender.schedule_messages(servicebus_message, now)
            message_length += 1
        for amqp_annotated_message in make_amqp_annotated_messages():
            sender.schedule_messages(amqp_annotated_message, now)
            message_length += 1
    if method == "schedule_messages" and message_payload_type == "list":
        servicebus_messages = make_servicebus_messages()
        sender.schedule_messages(servicebus_messages, now)

        amqp_annotated_messages = make_amqp_annotated_messages()
        sender.schedule_messages(amqp_annotated_messages, now)

        message_length = len(servicebus_messages) + len(amqp_annotated_messages)

    received_queue_messages = receiver.receive_messages(max_message_count=message_length, max_wait_time=5)
    assert len(received_queue_messages) == message_length

    if not distributed_tracing_enabled or not batch_links_enabled:
        assert not any(key in normalize_properties(m) for m in received_queue_messages for key in TRACE_CONTEXT_KEYS)
    else:
        assert all(key in normalize_properties(m) for m in received_queue_messages for key in TRACE_CONTEXT_KEYS)


async def run_test_async(
    sender, receiver, method, message_payload_type, distributed_tracing_enabled, batch_links_enabled
):
    message_length = 0
    now = datetime.now(timezone.utc)

    if method == "send_messages" and message_payload_type == "single":
        for servicebus_message in make_servicebus_messages():
            await sender.send_messages(servicebus_message)
            message_length += 1
        for amqp_annotated_message in make_amqp_annotated_messages():
            await sender.send_messages(amqp_annotated_message)
            message_length += 1
    if method == "send_messages" and message_payload_type == "list":
        servicebus_messages = make_servicebus_messages()
        await sender.send_messages(servicebus_messages)

        amqp_annotated_messages = make_amqp_annotated_messages()
        await sender.send_messages(amqp_annotated_messages)

        message_length = len(servicebus_messages) + len(amqp_annotated_messages)
    elif method == "send_messages" and message_payload_type == "batch":
        servicebus_message_batch = await sender.create_message_batch()
        for servicebus_message in make_servicebus_messages():
            servicebus_message_batch.add_message(servicebus_message)
            message_length += 1
        await sender.send_messages(servicebus_message_batch)

        amqp_annotated_message_batch = await sender.create_message_batch()
        for amqp_annotated_message in make_amqp_annotated_messages():
            amqp_annotated_message_batch.add_message(amqp_annotated_message)
            message_length += 1
        await sender.send_messages(amqp_annotated_message_batch)
    elif method == "schedule_messages" and message_payload_type == "single":
        for servicebus_message in make_servicebus_messages():
            await sender.schedule_messages(servicebus_message, now)
            message_length += 1
        for amqp_annotated_message in make_amqp_annotated_messages():
            await sender.schedule_messages(amqp_annotated_message, now)
            message_length += 1
    if method == "schedule_messages" and message_payload_type == "list":
        servicebus_messages = make_servicebus_messages()
        await sender.schedule_messages(servicebus_messages, now)

        amqp_annotated_messages = make_amqp_annotated_messages()
        await sender.schedule_messages(amqp_annotated_messages, now)

        message_length = len(servicebus_messages) + len(amqp_annotated_messages)

    received_queue_messages = await receiver.receive_messages(max_message_count=message_length, max_wait_time=5)
    assert len(received_queue_messages) == message_length

    if not distributed_tracing_enabled or not batch_links_enabled:
        assert not any(key in normalize_properties(m) for m in received_queue_messages for key in TRACE_CONTEXT_KEYS)
    else:
        assert all(key in normalize_properties(m) for m in received_queue_messages for key in TRACE_CONTEXT_KEYS)


@pytest.mark.asyncio
async def test_common():
    method = os.environ.get("METHOD")
    is_async = os.environ.get("IS_ASYNC") == "True"
    message_payload_type = os.environ.get("MESSAGE_PAYLOAD_TYPE")
    distributed_tracing_enabled = os.environ.get("DD_AZURE_SERVICEBUS_DISTRIBUTED_TRACING") == "True"
    batch_links_enabled = os.environ.get("DD_TRACE_AZURE_SERVICEBUS_BATCH_LINKS_ENABLED") == "True"

    if is_async:
        client = ServiceBusClientAsync.from_connection_string(CONNECTION_STRING)
        sender = client.get_queue_sender(queue_name=QUEUE_NAME)
        receiver = client.get_queue_receiver(
            queue_name=QUEUE_NAME, receive_mode=ServiceBusReceiveMode.RECEIVE_AND_DELETE
        )
        try:
            await run_test_async(
                sender, receiver, method, message_payload_type, distributed_tracing_enabled, batch_links_enabled
            )
        finally:
            await receiver.close()
            await sender.close()
            await client.close()
    else:
        client = ServiceBusClient.from_connection_string(CONNECTION_STRING)
        sender = client.get_queue_sender(queue_name=QUEUE_NAME)
        receiver = client.get_queue_receiver(
            queue_name=QUEUE_NAME, receive_mode=ServiceBusReceiveMode.RECEIVE_AND_DELETE
        )
        try:
            run_test(sender, receiver, method, message_payload_type, distributed_tracing_enabled, batch_links_enabled)
        finally:
            receiver.close()
            sender.close()
            client.close()


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main(["-x", __file__]))
