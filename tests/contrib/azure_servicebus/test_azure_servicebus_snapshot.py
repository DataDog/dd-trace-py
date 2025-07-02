import asyncio
from datetime import datetime
from datetime import timezone
import os
from typing import Dict
from typing import List
from typing import Optional
from typing import Union
import uuid

from azure.servicebus import ServiceBusClient
from azure.servicebus import ServiceBusMessage
from azure.servicebus import ServiceBusReceiveMode
from azure.servicebus import ServiceBusReceiver
from azure.servicebus import ServiceBusSender
from azure.servicebus.aio import ServiceBusClient as ServiceBusClientAsync
from azure.servicebus.aio import ServiceBusReceiver as ServiceBusReceiverAsync
from azure.servicebus.aio import ServiceBusSender as ServiceBusSenderAsync
import pytest

from ddtrace.contrib.internal.azure_servicebus.patch import patch
from ddtrace.contrib.internal.azure_servicebus.patch import unpatch


CONNECTION_STRING = "Endpoint=sb://localhost;SharedAccessKeyName=RootManageSharedAccessKey;SharedAccessKey=SAS_KEY_VALUE;UseDevelopmentEmulator=true;"
QUEUE_NAME = "queue.1"
TOPIC_NAME = "topic.1"
SUBSCRIPTION_NAME = "subscription.3"
DEFAULT_APPLICATION_PROPERTIES = {"property": "val", b"byteproperty": b"byteval"}

DISTRIBUTED_TRACING_DISABLED_PARAMS = {
    "DD_AZURE_SERVICEBUS_DISTRIBUTED_TRACING": "False",
}

SPAN_ATTRIBUTE_SCHEMA_V1_PARAMS = {"DD_TRACE_SPAN_ATTRIBUTE_SCHEMA": "v1"}


@pytest.fixture(autouse=True)
def patch_azure_servicebus():
    patch()
    yield
    unpatch()


@pytest.fixture()
def azure_servicebus_client():
    with ServiceBusClient.from_connection_string(conn_str=CONNECTION_STRING) as servicebus_client:
        yield servicebus_client


@pytest.fixture()
def azure_servicebus_queue_sender(azure_servicebus_client: ServiceBusClient):
    with azure_servicebus_client.get_queue_sender(queue_name=QUEUE_NAME) as queue_sender:
        yield queue_sender


@pytest.fixture()
def azure_servicebus_topic_sender(azure_servicebus_client: ServiceBusClient):
    with azure_servicebus_client.get_topic_sender(topic_name=TOPIC_NAME) as topic_sender:
        yield topic_sender


@pytest.fixture()
def azure_servicebus_queue_receiver(azure_servicebus_client: ServiceBusClient):
    with azure_servicebus_client.get_queue_receiver(
        queue_name=QUEUE_NAME, receive_mode=ServiceBusReceiveMode.RECEIVE_AND_DELETE
    ) as queue_receiver:
        yield queue_receiver


@pytest.fixture()
def azure_servicebus_subscription_receiver(azure_servicebus_client: ServiceBusClient):
    with azure_servicebus_client.get_subscription_receiver(
        topic_name=TOPIC_NAME,
        subscription_name=SUBSCRIPTION_NAME,
        receive_mode=ServiceBusReceiveMode.RECEIVE_AND_DELETE,
    ) as subscription_receiver:
        yield subscription_receiver


@pytest.fixture()
async def azure_servicebus_client_async():
    async with ServiceBusClientAsync.from_connection_string(conn_str=CONNECTION_STRING) as servicebus_client:
        yield servicebus_client


@pytest.fixture()
async def azure_servicebus_queue_sender_async(azure_servicebus_client_async: ServiceBusClientAsync):
    async with azure_servicebus_client_async.get_queue_sender(queue_name=QUEUE_NAME) as queue_sender:
        yield queue_sender


@pytest.fixture()
async def azure_servicebus_topic_sender_async(azure_servicebus_client_async: ServiceBusClientAsync):
    async with azure_servicebus_client_async.get_topic_sender(topic_name=TOPIC_NAME) as topic_sender:
        yield topic_sender


@pytest.fixture()
async def azure_servicebus_queue_receiver_async(azure_servicebus_client_async: ServiceBusClientAsync):
    async with azure_servicebus_client_async.get_queue_receiver(
        queue_name=QUEUE_NAME, receive_mode=ServiceBusReceiveMode.RECEIVE_AND_DELETE
    ) as queue_receiver:
        yield queue_receiver


@pytest.fixture()
async def azure_servicebus_subscription_receiver_async(azure_servicebus_client_async: ServiceBusClientAsync):
    async with azure_servicebus_client_async.get_subscription_receiver(
        topic_name=TOPIC_NAME,
        subscription_name=SUBSCRIPTION_NAME,
        receive_mode=ServiceBusReceiveMode.RECEIVE_AND_DELETE,
    ) as subscription_receiver:
        yield subscription_receiver


@pytest.fixture()
def trace_context_keys():
    return [
        "x-datadog-trace-id",
        "x-datadog-parent-id",
        "x-datadog-sampling-priority",
        "x-datadog-tags",
        "traceparent",
        "tracestate",
    ]


def make_messages():
    return [
        ServiceBusMessage("test message without properties"),
        ServiceBusMessage("test message without properties"),
        [
            ServiceBusMessage("test message without properties"),
            ServiceBusMessage(
                "test message with properties",
                application_properties=DEFAULT_APPLICATION_PROPERTIES,
            ),
        ],
    ]


async def send_messages_to_queue_async(queue_sender_async):
    for message in make_messages():
        await queue_sender_async.send_messages(message)


async def send_messages_to_topic_async(topic_sender_async):
    for message in make_messages():
        await topic_sender_async.send_messages(message)


async def schedule_messages_to_queue_async(queue_sender_async, schedule_time_utc):
    for message in make_messages():
        await queue_sender_async.schedule_messages(message, schedule_time_utc)


async def schedule_messages_to_topic_async(topic_sender_async, schedule_time_utc):
    for message in make_messages():
        await topic_sender_async.schedule_messages(message, schedule_time_utc)


def normalize_application_properties(
    application_properties: Optional[Dict[Union[str, bytes], Union[int, float, bytes, bool, str, uuid.UUID]]],
):
    if not application_properties:
        return {}

    return {k.decode() if isinstance(k, bytes) else k: v for k, v in application_properties.items()}


@pytest.mark.parametrize(
    "env_vars",
    [{}, DISTRIBUTED_TRACING_DISABLED_PARAMS, SPAN_ATTRIBUTE_SCHEMA_V1_PARAMS],
    ids=["default_config", "distributed_tracing_disabled", "span_attribute_schema_v1"],
)
@pytest.mark.snapshot
def test_send_messages(env_vars, ddtrace_run_python_code_in_subprocess):
    code = f"""
import os
from typing import Dict
from typing import Optional
from typing import Union
import uuid

from azure.servicebus import ServiceBusClient
from azure.servicebus import ServiceBusMessage
from azure.servicebus import ServiceBusReceiveMode

from ddtrace.internal.utils.formats import asbool


TRACE_CONTEXT_KEYS = [
    "x-datadog-trace-id",
    "x-datadog-parent-id",
    "x-datadog-sampling-priority",
    "x-datadog-tags",
    "traceparent",
    "tracestate",
]


def normalize_application_properties(
    application_properties: Optional[Dict[Union[str, bytes], Union[int, float, bytes, bool, str, uuid.UUID]]],
):
    if not application_properties:
        return {{}}

    return {{k.decode() if isinstance(k, bytes) else k: v for k, v in application_properties.items()}}


def make_messages():
    return [
        ServiceBusMessage("test message without properties"),
        ServiceBusMessage("test message without properties"),
        [
            ServiceBusMessage("test message without properties"),
            ServiceBusMessage(
                "test message with properties",
                application_properties={DEFAULT_APPLICATION_PROPERTIES},
            ),
        ],
    ]


with ServiceBusClient.from_connection_string(conn_str="{CONNECTION_STRING}") as servicebus_client:
    with servicebus_client.get_queue_sender(queue_name="{QUEUE_NAME}") as queue_sender:
        for message in make_messages():
            queue_sender.send_messages(message)
    with servicebus_client.get_topic_sender(topic_name="{TOPIC_NAME}") as topic_sender:
        for message in make_messages():
            topic_sender.send_messages(message)
    with servicebus_client.get_queue_receiver(
        queue_name="{QUEUE_NAME}", receive_mode=ServiceBusReceiveMode.RECEIVE_AND_DELETE
    ) as queue_receiver:
        received_messages = queue_receiver.receive_messages(max_message_count=4, max_wait_time=5)
        assert len(received_messages) == 4
        if asbool(os.getenv("DD_AZURE_SERVICEBUS_DISTRIBUTED_TRACING", default=True)):
            assert all(
                key in normalize_application_properties(msg.application_properties)
                for msg in received_messages
                for key in TRACE_CONTEXT_KEYS
            )
        else:
            assert not any(
                key in normalize_application_properties(msg.application_properties)
                for msg in received_messages
                for key in TRACE_CONTEXT_KEYS
            )
    with servicebus_client.get_subscription_receiver(
        topic_name="{TOPIC_NAME}",
        subscription_name="{SUBSCRIPTION_NAME}",
        receive_mode=ServiceBusReceiveMode.RECEIVE_AND_DELETE,
    ) as subscription_receiver:
        received_messages = subscription_receiver.receive_messages(max_message_count=4, max_wait_time=5)
        assert len(received_messages) == 4
        if asbool(os.getenv("DD_AZURE_SERVICEBUS_DISTRIBUTED_TRACING", default=True)):
            assert all(
                key in normalize_application_properties(msg.application_properties)
                for msg in received_messages
                for key in TRACE_CONTEXT_KEYS
            )
        else:
            assert not any(
                key in normalize_application_properties(msg.application_properties)
                for msg in received_messages
                for key in TRACE_CONTEXT_KEYS
            )
"""

    env = os.environ.copy()
    env.update(env_vars)
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, (err.decode(), out.decode())
    assert err == b"", err.decode()


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_send_messages_async(
    azure_servicebus_queue_sender_async: ServiceBusSenderAsync,
    azure_servicebus_queue_receiver_async: ServiceBusReceiverAsync,
    azure_servicebus_topic_sender_async: ServiceBusSenderAsync,
    azure_servicebus_subscription_receiver_async: ServiceBusReceiverAsync,
    trace_context_keys: List[str],
):
    await asyncio.gather(
        send_messages_to_queue_async(azure_servicebus_queue_sender_async),
        send_messages_to_topic_async(azure_servicebus_topic_sender_async),
    )

    received_queue_messages, received_subscription_messages = await asyncio.gather(
        azure_servicebus_queue_receiver_async.receive_messages(max_message_count=4, max_wait_time=5),
        azure_servicebus_subscription_receiver_async.receive_messages(max_message_count=4, max_wait_time=5),
    )

    assert len(received_queue_messages) == 4
    assert len(received_subscription_messages) == 4

    assert all(
        key in normalize_application_properties(msg.application_properties)
        for msg in received_queue_messages
        for key in trace_context_keys
    )

    assert all(
        key in normalize_application_properties(msg.application_properties)
        for msg in received_subscription_messages
        for key in trace_context_keys
    )


@pytest.mark.snapshot
def test_schedule_messages(
    azure_servicebus_queue_sender: ServiceBusSender,
    azure_servicebus_queue_receiver: ServiceBusReceiver,
    azure_servicebus_topic_sender: ServiceBusSender,
    azure_servicebus_subscription_receiver: ServiceBusReceiver,
    trace_context_keys: List[str],
):
    now = datetime.now(timezone.utc)

    for message in make_messages():
        azure_servicebus_queue_sender.schedule_messages(message, now)

    for message in make_messages():
        azure_servicebus_topic_sender.schedule_messages(message, now)

    received_queue_messages = azure_servicebus_queue_receiver.receive_messages(max_message_count=4, max_wait_time=5)
    received_subscription_messages = azure_servicebus_subscription_receiver.receive_messages(
        max_message_count=4, max_wait_time=5
    )

    assert len(received_queue_messages) == 4
    assert len(received_subscription_messages) == 4

    assert all(
        key in normalize_application_properties(msg.application_properties)
        for msg in received_queue_messages
        for key in trace_context_keys
    )

    assert all(
        key in normalize_application_properties(msg.application_properties)
        for msg in received_subscription_messages
        for key in trace_context_keys
    )


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_schedule_messages_async(
    azure_servicebus_queue_sender_async: ServiceBusSenderAsync,
    azure_servicebus_queue_receiver_async: ServiceBusReceiverAsync,
    azure_servicebus_topic_sender_async: ServiceBusSenderAsync,
    azure_servicebus_subscription_receiver_async: ServiceBusReceiverAsync,
    trace_context_keys: List[str],
):
    now = datetime.now(timezone.utc)

    await asyncio.gather(
        schedule_messages_to_queue_async(azure_servicebus_queue_sender_async, now),
        schedule_messages_to_topic_async(azure_servicebus_topic_sender_async, now),
    )

    received_queue_messages, received_subscription_messages = await asyncio.gather(
        azure_servicebus_queue_receiver_async.receive_messages(max_message_count=4, max_wait_time=5),
        azure_servicebus_subscription_receiver_async.receive_messages(max_message_count=4, max_wait_time=5),
    )

    assert len(received_queue_messages) == 4
    assert len(received_subscription_messages) == 4

    assert all(
        key in normalize_application_properties(msg.application_properties)
        for msg in received_queue_messages
        for key in trace_context_keys
    )

    assert all(
        key in normalize_application_properties(msg.application_properties)
        for msg in received_subscription_messages
        for key in trace_context_keys
    )
