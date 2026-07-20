from datetime import datetime
from datetime import timezone
import os
import time
from typing import Union
from uuid import uuid4

from azure.servicebus import ServiceBusClient
from azure.servicebus import ServiceBusMessage
from azure.servicebus import ServiceBusReceiveMode
from azure.servicebus import ServiceBusReceiver
from azure.servicebus import ServiceBusSender
from azure.servicebus.aio import ServiceBusClient as ServiceBusClientAsync
from azure.servicebus.aio import ServiceBusReceiver as ServiceBusReceiverAsync
from azure.servicebus.aio import ServiceBusSender as ServiceBusSenderAsync
from azure.servicebus.amqp import AmqpAnnotatedMessage
import pytest


CONNECTION_STRING = (
    "Endpoint=sb://localhost;SharedAccessKeyName=RootManageSharedAccessKey;"
    "SharedAccessKey=SAS_KEY_VALUE;UseDevelopmentEmulator=true;"
)
QUEUE_NAME = "queue.1"
TOPIC_NAME = "topic.1"
SUBSCRIPTION_NAME = "subscription.3"
DEFAULT_APPLICATION_PROPERTIES = {"property": "val", b"byteproperty": b"byteval"}
TEST_RUN_ID_KEY = "dd_test_run_id"
TRACE_CONTEXT_KEYS = [
    "x-datadog-trace-id",
    "x-datadog-parent-id",
    "x-datadog-sampling-priority",
    "x-datadog-tags",
    "traceparent",
    "tracestate",
]
RECEIVE_TIMEOUT_SECONDS = 15


def normalize_properties(message: Union[ServiceBusMessage, AmqpAnnotatedMessage]):
    props = message.application_properties

    if not props:
        props = {}

    return {k.decode() if isinstance(k, bytes) else k: v for k, v in props.items()}


def set_test_run_id(message: Union[ServiceBusMessage, AmqpAnnotatedMessage], test_run_id: str):
    props = dict(message.application_properties or {})
    props[TEST_RUN_ID_KEY] = test_run_id
    message.application_properties = props


def tag_messages_with_run_id(messages, test_run_id: str):
    for message in messages:
        set_test_run_id(message, test_run_id)


def message_matches_test_run(message, test_run_id: str) -> bool:
    return normalize_properties(message).get(TEST_RUN_ID_KEY) == test_run_id


def receive_matching_messages(receiver: ServiceBusReceiver, test_run_id: str, message_length: int):
    received_queue_messages = []
    deadline = time.monotonic() + RECEIVE_TIMEOUT_SECONDS
    while len(received_queue_messages) < message_length and time.monotonic() < deadline:
        messages = receiver.receive_messages(
            max_message_count=10,
            max_wait_time=min(5, max(1, deadline - time.monotonic())),
        )
        for message in messages:
            if message_matches_test_run(message, test_run_id):
                receiver.complete_message(message)
                received_queue_messages.append(message)
            else:
                receiver.abandon_message(message)
    return received_queue_messages


async def receive_matching_messages_async(receiver: ServiceBusReceiverAsync, test_run_id: str, message_length: int):
    received_queue_messages = []
    deadline = time.monotonic() + RECEIVE_TIMEOUT_SECONDS
    while len(received_queue_messages) < message_length and time.monotonic() < deadline:
        messages = await receiver.receive_messages(
            max_message_count=10,
            max_wait_time=min(5, max(1, deadline - time.monotonic())),
        )
        for message in messages:
            if message_matches_test_run(message, test_run_id):
                await receiver.complete_message(message)
                received_queue_messages.append(message)
            else:
                await receiver.abandon_message(message)
    return received_queue_messages


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


def run_test(
    sender: ServiceBusSender,
    receiver: ServiceBusReceiver,
    method: Union[str, None],
    message_payload_type: Union[str, None],
    distributed_tracing_enabled: bool,
    batch_links_enabled: bool,
    test_run_id: str,
):
    servicebus_messages = make_servicebus_messages()
    amqp_annotated_messages = make_amqp_annotated_messages()
    tag_messages_with_run_id(servicebus_messages, test_run_id)
    tag_messages_with_run_id(amqp_annotated_messages, test_run_id)

    message_length = len(servicebus_messages) + len(amqp_annotated_messages)
    now = datetime.now(timezone.utc)

    if method == "send_messages" and message_payload_type == "single":
        for servicebus_message in servicebus_messages:
            sender.send_messages(servicebus_message)
        for amqp_annotated_message in amqp_annotated_messages:
            sender.send_messages(amqp_annotated_message)
    elif method == "send_messages" and message_payload_type == "list":
        sender.send_messages(servicebus_messages)
        sender.send_messages(amqp_annotated_messages)
    elif method == "send_messages" and message_payload_type == "batch":
        servicebus_message_batch = sender.create_message_batch()
        for servicebus_message in servicebus_messages:
            servicebus_message_batch.add_message(servicebus_message)
        sender.send_messages(servicebus_message_batch)

        amqp_annotated_message_batch = sender.create_message_batch()
        for amqp_annotated_message in amqp_annotated_messages:
            amqp_annotated_message_batch.add_message(amqp_annotated_message)
        sender.send_messages(amqp_annotated_message_batch)
    elif method == "schedule_messages" and message_payload_type == "single":
        for servicebus_message in servicebus_messages:
            sender.schedule_messages(servicebus_message, now)
        for amqp_annotated_message in amqp_annotated_messages:
            sender.schedule_messages(amqp_annotated_message, now)
    elif method == "schedule_messages" and message_payload_type == "list":
        sender.schedule_messages(servicebus_messages, now)
        sender.schedule_messages(amqp_annotated_messages, now)

    received_queue_messages = receive_matching_messages(receiver, test_run_id, message_length)
    assert len(received_queue_messages) == message_length

    if not distributed_tracing_enabled or not batch_links_enabled:
        assert not any(key in normalize_properties(m) for m in received_queue_messages for key in TRACE_CONTEXT_KEYS)
    else:
        assert all(key in normalize_properties(m) for m in received_queue_messages for key in TRACE_CONTEXT_KEYS)


async def run_test_async(
    sender: ServiceBusSenderAsync,
    receiver: ServiceBusReceiverAsync,
    method: Union[str, None],
    message_payload_type: Union[str, None],
    distributed_tracing_enabled: bool,
    batch_links_enabled: bool,
    test_run_id: str,
):
    servicebus_messages = make_servicebus_messages()
    amqp_annotated_messages = make_amqp_annotated_messages()
    tag_messages_with_run_id(servicebus_messages, test_run_id)
    tag_messages_with_run_id(amqp_annotated_messages, test_run_id)

    message_length = len(servicebus_messages) + len(amqp_annotated_messages)
    now = datetime.now(timezone.utc)

    if method == "send_messages" and message_payload_type == "single":
        for servicebus_message in servicebus_messages:
            await sender.send_messages(servicebus_message)
        for amqp_annotated_message in amqp_annotated_messages:
            await sender.send_messages(amqp_annotated_message)
    elif method == "send_messages" and message_payload_type == "list":
        await sender.send_messages(servicebus_messages)
        await sender.send_messages(amqp_annotated_messages)
    elif method == "send_messages" and message_payload_type == "batch":
        servicebus_message_batch = await sender.create_message_batch()
        for servicebus_message in servicebus_messages:
            servicebus_message_batch.add_message(servicebus_message)
        await sender.send_messages(servicebus_message_batch)

        amqp_annotated_message_batch = await sender.create_message_batch()
        for amqp_annotated_message in amqp_annotated_messages:
            amqp_annotated_message_batch.add_message(amqp_annotated_message)
        await sender.send_messages(amqp_annotated_message_batch)
    elif method == "schedule_messages" and message_payload_type == "single":
        for servicebus_message in servicebus_messages:
            await sender.schedule_messages(servicebus_message, now)
        for amqp_annotated_message in amqp_annotated_messages:
            await sender.schedule_messages(amqp_annotated_message, now)
    elif method == "schedule_messages" and message_payload_type == "list":
        await sender.schedule_messages(servicebus_messages, now)
        await sender.schedule_messages(amqp_annotated_messages, now)

    received_queue_messages = await receive_matching_messages_async(receiver, test_run_id, message_length)
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
    distributed_tracing_enabled = os.environ.get("DD_AZURE_SERVICEBUS_DISTRIBUTED_TRACING", "True") == "True"
    batch_links_enabled = os.environ.get("DD_TRACE_AZURE_SERVICEBUS_BATCH_LINKS_ENABLED", "True") == "True"
    test_run_id = str(uuid4())

    if is_async:
        client = ServiceBusClientAsync.from_connection_string(CONNECTION_STRING)
        sender = client.get_queue_sender(queue_name=QUEUE_NAME)
        receiver = client.get_queue_receiver(queue_name=QUEUE_NAME, receive_mode=ServiceBusReceiveMode.PEEK_LOCK)
        try:
            await run_test_async(
                sender,
                receiver,
                method,
                message_payload_type,
                distributed_tracing_enabled,
                batch_links_enabled,
                test_run_id,
            )
        finally:
            await receiver.close()
            await sender.close()
            await client.close()
    else:
        client = ServiceBusClient.from_connection_string(CONNECTION_STRING)
        sender = client.get_queue_sender(queue_name=QUEUE_NAME)
        receiver = client.get_queue_receiver(queue_name=QUEUE_NAME, receive_mode=ServiceBusReceiveMode.PEEK_LOCK)
        try:
            run_test(
                sender,
                receiver,
                method,
                message_payload_type,
                distributed_tracing_enabled,
                batch_links_enabled,
                test_run_id,
            )
        finally:
            receiver.close()
            sender.close()
            client.close()


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main(["-x", __file__]))
