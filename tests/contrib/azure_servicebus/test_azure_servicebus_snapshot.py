from datetime import datetime
from typing import Dict
from typing import List
from typing import Optional
from typing import Union
import uuid

from azure.servicebus import ServiceBusClient
from azure.servicebus import ServiceBusMessage
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
    with azure_servicebus_client.get_queue_sender(QUEUE_NAME) as queue_sender:
        yield queue_sender


@pytest.fixture()
def azure_servicebus_topic_sender(azure_servicebus_client: ServiceBusClient):
    with azure_servicebus_client.get_topic_sender(topic_name=TOPIC_NAME) as topic_sender:
        yield topic_sender


@pytest.fixture()
def azure_servicebus_queue_receiver(azure_servicebus_client: ServiceBusClient):
    with azure_servicebus_client.get_queue_receiver(queue_name=QUEUE_NAME) as queue_receiver:
        yield queue_receiver


@pytest.fixture()
def azure_servicebus_subscription_receiver(azure_servicebus_client: ServiceBusClient):
    with azure_servicebus_client.get_subscription_receiver(
        topic_name=TOPIC_NAME, subscription_name=SUBSCRIPTION_NAME
    ) as subscription_receiver:
        yield subscription_receiver


@pytest.fixture()
async def azure_servicebus_client_async():
    async with ServiceBusClientAsync.from_connection_string(conn_str=CONNECTION_STRING) as servicebus_client:
        yield servicebus_client


@pytest.fixture()
async def azure_servicebus_queue_sender_async(azure_servicebus_client_async: ServiceBusClientAsync):
    async with azure_servicebus_client_async.get_queue_sender(QUEUE_NAME) as queue_sender:
        yield queue_sender


@pytest.fixture()
async def azure_servicebus_topic_sender_async(azure_servicebus_client_async: ServiceBusClientAsync):
    async with azure_servicebus_client_async.get_topic_sender(topic_name=TOPIC_NAME) as topic_sender:
        yield topic_sender


@pytest.fixture()
async def azure_servicebus_queue_receiver_async(azure_servicebus_client_async: ServiceBusClientAsync):
    async with azure_servicebus_client_async.get_queue_receiver(queue_name=QUEUE_NAME) as queue_receiver:
        yield queue_receiver


@pytest.fixture()
async def azure_servicebus_subscription_receiver_async(azure_servicebus_client_async: ServiceBusClientAsync):
    async with azure_servicebus_client_async.get_subscription_receiver(
        topic_name=TOPIC_NAME, subscription_name=SUBSCRIPTION_NAME
    ) as subscription_receiver:
        yield subscription_receiver


@pytest.fixture()
def message_with_properties():
    return ServiceBusMessage(
        "test message 1",
        application_properties=DEFAULT_APPLICATION_PROPERTIES,
    )


@pytest.fixture()
def message_without_properties():
    return ServiceBusMessage("test message 2")


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


def normalize_application_properties(
    application_properties: Optional[Dict[Union[str, bytes], Union[int, float, bytes, bool, str, uuid.UUID]]],
):
    if not application_properties:
        return {}

    return {k.decode() if isinstance(k, bytes) else k: v for k, v in application_properties.items()}


@pytest.mark.snapshot
def test_queue_send_single_message_keyword_args(
    azure_servicebus_queue_sender: ServiceBusSender,
    azure_servicebus_queue_receiver: ServiceBusReceiver,
    message_without_properties: ServiceBusMessage,
    trace_context_keys: List[str],
):
    azure_servicebus_queue_sender.send_messages(message=message_without_properties)
    received_messages = azure_servicebus_queue_receiver.receive_messages(max_message_count=1)

    assert len(received_messages) == 1
    assert all(
        key in normalize_application_properties(received_messages[0].application_properties)
        for key in trace_context_keys
    )


@pytest.mark.snapshot
def test_queue_send_single_message(
    azure_servicebus_queue_sender: ServiceBusSender,
    azure_servicebus_queue_receiver: ServiceBusReceiver,
    message_without_properties: ServiceBusMessage,
    trace_context_keys: List[str],
):
    azure_servicebus_queue_sender.send_messages(message_without_properties)
    received_messages = azure_servicebus_queue_receiver.receive_messages(max_message_count=1)

    assert len(received_messages) == 1
    assert all(
        key in normalize_application_properties(received_messages[0].application_properties)
        for key in trace_context_keys
    )


@pytest.mark.snapshot
def test_queue_send_message_list(
    azure_servicebus_queue_sender: ServiceBusSender,
    azure_servicebus_queue_receiver: ServiceBusReceiver,
    message_without_properties: ServiceBusMessage,
    message_with_properties: ServiceBusMessage,
    trace_context_keys: List[str],
):
    azure_servicebus_queue_sender.send_messages([message_without_properties, message_with_properties])
    received_messages = azure_servicebus_queue_receiver.receive_messages(max_message_count=2)

    assert len(received_messages) == 2
    assert all(
        key in normalize_application_properties(received_messages[0].application_properties)
        for key in trace_context_keys
    )
    assert all(
        key in normalize_application_properties(received_messages[1].application_properties)
        for key in trace_context_keys
    )


@pytest.mark.snapshot
def test_topic_send_single_message(
    azure_servicebus_topic_sender: ServiceBusSender,
    azure_servicebus_subscription_receiver: ServiceBusReceiver,
    message_without_properties: ServiceBusMessage,
    trace_context_keys: List[str],
):
    azure_servicebus_topic_sender.send_messages(message_without_properties)
    received_messages = azure_servicebus_subscription_receiver.receive_messages(max_message_count=1)

    assert len(received_messages) == 1
    assert all(
        key in normalize_application_properties(received_messages[0].application_properties)
        for key in trace_context_keys
    )


@pytest.mark.snapshot
def test_topic_send_message_list(
    azure_servicebus_topic_sender: ServiceBusSender,
    azure_servicebus_subscription_receiver: ServiceBusReceiver,
    message_without_properties: ServiceBusMessage,
    message_with_properties: ServiceBusMessage,
    trace_context_keys: List[str],
):
    azure_servicebus_topic_sender.send_messages([message_without_properties, message_with_properties])
    received_messages = azure_servicebus_subscription_receiver.receive_messages(max_message_count=2)

    assert len(received_messages) == 2
    assert all(
        key in normalize_application_properties(received_messages[0].application_properties)
        for key in trace_context_keys
    )
    assert all(
        key in normalize_application_properties(received_messages[1].application_properties)
        for key in trace_context_keys
    )


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_queue_send_single_message_async(
    azure_servicebus_queue_sender_async: ServiceBusSenderAsync,
    azure_servicebus_queue_receiver_async: ServiceBusReceiverAsync,
    message_without_properties: ServiceBusMessage,
    trace_context_keys: List[str],
):
    await azure_servicebus_queue_sender_async.send_messages(message_without_properties)
    received_messages = await azure_servicebus_queue_receiver_async.receive_messages(max_message_count=1)

    assert len(received_messages) == 1
    assert all(
        key in normalize_application_properties(received_messages[0].application_properties)
        for key in trace_context_keys
    )


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_queue_send_message_list_async(
    azure_servicebus_queue_sender_async: ServiceBusSenderAsync,
    azure_servicebus_queue_receiver_async: ServiceBusReceiverAsync,
    message_without_properties: ServiceBusMessage,
    message_with_properties: ServiceBusMessage,
    trace_context_keys: List[str],
):
    await azure_servicebus_queue_sender_async.send_messages([message_without_properties, message_with_properties])
    received_messages = await azure_servicebus_queue_receiver_async.receive_messages(max_message_count=2)

    assert len(received_messages) == 2
    assert all(
        key in normalize_application_properties(received_messages[0].application_properties)
        for key in trace_context_keys
    )
    assert all(
        key in normalize_application_properties(received_messages[1].application_properties)
        for key in trace_context_keys
    )


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_topic_send_single_message_async(
    azure_servicebus_topic_sender_async: ServiceBusSenderAsync,
    azure_servicebus_subscription_receiver_async: ServiceBusReceiverAsync,
    message_without_properties: ServiceBusMessage,
    trace_context_keys: List[str],
):
    await azure_servicebus_topic_sender_async.send_messages(message_without_properties)
    received_messages = await azure_servicebus_subscription_receiver_async.receive_messages(max_message_count=1)

    assert len(received_messages) == 1
    assert all(
        key in normalize_application_properties(received_messages[0].application_properties)
        for key in trace_context_keys
    )


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_topic_send_message_list_async(
    azure_servicebus_topic_sender_async: ServiceBusSenderAsync,
    azure_servicebus_subscription_receiver_async: ServiceBusReceiverAsync,
    message_without_properties: ServiceBusMessage,
    message_with_properties: ServiceBusMessage,
    trace_context_keys: List[str],
):
    await azure_servicebus_topic_sender_async.send_messages([message_without_properties, message_with_properties])
    received_messages = await azure_servicebus_subscription_receiver_async.receive_messages(max_message_count=2)

    assert len(received_messages) == 2
    assert all(
        key in normalize_application_properties(received_messages[0].application_properties)
        for key in trace_context_keys
    )
    assert all(
        key in normalize_application_properties(received_messages[1].application_properties)
        for key in trace_context_keys
    )


@pytest.mark.snapshot
def test_queue_schedule_single_message_keyword_args(
    azure_servicebus_queue_sender: ServiceBusSender,
    azure_servicebus_queue_receiver: ServiceBusReceiver,
    message_without_properties: ServiceBusMessage,
    trace_context_keys: List[str],
):
    azure_servicebus_queue_sender.schedule_messages(
        messages=message_without_properties, schedule_time_utc=datetime.now()
    )
    received_messages = azure_servicebus_queue_receiver.receive_messages(max_message_count=1)

    assert len(received_messages) == 1
    assert all(
        key in normalize_application_properties(received_messages[0].application_properties)
        for key in trace_context_keys
    )


@pytest.mark.snapshot
def test_queue_schedule_single_message(
    azure_servicebus_queue_sender: ServiceBusSender,
    azure_servicebus_queue_receiver: ServiceBusReceiver,
    message_without_properties: ServiceBusMessage,
    trace_context_keys: List[str],
):
    azure_servicebus_queue_sender.schedule_messages(message_without_properties, datetime.now())
    received_messages = azure_servicebus_queue_receiver.receive_messages(max_message_count=1)

    assert len(received_messages) == 1
    assert all(
        key in normalize_application_properties(received_messages[0].application_properties)
        for key in trace_context_keys
    )


@pytest.mark.snapshot
def test_queue_schedule_message_list(
    azure_servicebus_queue_sender: ServiceBusSender,
    azure_servicebus_queue_receiver: ServiceBusReceiver,
    message_without_properties: ServiceBusMessage,
    message_with_properties: ServiceBusMessage,
    trace_context_keys: List[str],
):
    azure_servicebus_queue_sender.schedule_messages(
        [message_without_properties, message_with_properties], datetime.now()
    )
    received_messages = azure_servicebus_queue_receiver.receive_messages(max_message_count=2)

    assert len(received_messages) == 2
    assert all(
        key in normalize_application_properties(received_messages[0].application_properties)
        for key in trace_context_keys
    )
    assert all(
        key in normalize_application_properties(received_messages[1].application_properties)
        for key in trace_context_keys
    )


@pytest.mark.snapshot
def test_topic_schedule_single_message(
    azure_servicebus_topic_sender: ServiceBusSender,
    azure_servicebus_subscription_receiver: ServiceBusReceiver,
    message_without_properties: ServiceBusMessage,
    trace_context_keys: List[str],
):
    azure_servicebus_topic_sender.schedule_messages(message_without_properties, datetime.now())
    received_messages = azure_servicebus_subscription_receiver.receive_messages(max_message_count=1)

    assert len(received_messages) == 1
    assert all(
        key in normalize_application_properties(received_messages[0].application_properties)
        for key in trace_context_keys
    )


@pytest.mark.snapshot
def test_topic_schedule_message_list(
    azure_servicebus_topic_sender: ServiceBusSender,
    azure_servicebus_subscription_receiver: ServiceBusReceiver,
    message_without_properties: ServiceBusMessage,
    message_with_properties: ServiceBusMessage,
    trace_context_keys: List[str],
):
    azure_servicebus_topic_sender.schedule_messages(
        [message_without_properties, message_with_properties], datetime.now()
    )
    received_messages = azure_servicebus_subscription_receiver.receive_messages(max_message_count=2)

    assert len(received_messages) == 2
    assert all(
        key in normalize_application_properties(received_messages[0].application_properties)
        for key in trace_context_keys
    )
    assert all(
        key in normalize_application_properties(received_messages[1].application_properties)
        for key in trace_context_keys
    )


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_queue_schedule_single_message_async(
    azure_servicebus_queue_sender_async: ServiceBusSenderAsync,
    azure_servicebus_queue_receiver_async: ServiceBusReceiverAsync,
    message_without_properties: ServiceBusMessage,
    trace_context_keys: List[str],
):
    await azure_servicebus_queue_sender_async.schedule_messages(message_without_properties, datetime.now())
    received_messages = await azure_servicebus_queue_receiver_async.receive_messages(max_message_count=1)

    assert len(received_messages) == 1
    assert all(
        key in normalize_application_properties(received_messages[0].application_properties)
        for key in trace_context_keys
    )


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_queue_schedule_message_list_async(
    azure_servicebus_queue_sender_async: ServiceBusSenderAsync,
    azure_servicebus_queue_receiver_async: ServiceBusReceiverAsync,
    message_without_properties: ServiceBusMessage,
    message_with_properties: ServiceBusMessage,
    trace_context_keys: List[str],
):
    await azure_servicebus_queue_sender_async.schedule_messages(
        [message_without_properties, message_with_properties], datetime.now()
    )
    received_messages = await azure_servicebus_queue_receiver_async.receive_messages(max_message_count=2)

    assert len(received_messages) == 2
    assert all(
        key in normalize_application_properties(received_messages[0].application_properties)
        for key in trace_context_keys
    )
    assert all(
        key in normalize_application_properties(received_messages[1].application_properties)
        for key in trace_context_keys
    )


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_topic_schedule_single_message_async(
    azure_servicebus_topic_sender_async: ServiceBusSenderAsync,
    azure_servicebus_subscription_receiver_async: ServiceBusReceiverAsync,
    message_without_properties: ServiceBusMessage,
    trace_context_keys: List[str],
):
    await azure_servicebus_topic_sender_async.schedule_messages(message_without_properties, datetime.now())
    received_messages = await azure_servicebus_subscription_receiver_async.receive_messages(max_message_count=1)

    assert len(received_messages) == 1
    assert all(
        key in normalize_application_properties(received_messages[0].application_properties)
        for key in trace_context_keys
    )


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_topic_schedule_message_list_async(
    azure_servicebus_topic_sender_async: ServiceBusSenderAsync,
    azure_servicebus_subscription_receiver_async: ServiceBusReceiverAsync,
    message_without_properties: ServiceBusMessage,
    message_with_properties: ServiceBusMessage,
    trace_context_keys: List[str],
):
    await azure_servicebus_topic_sender_async.schedule_messages(
        [message_without_properties, message_with_properties], datetime.now()
    )
    received_messages = await azure_servicebus_subscription_receiver_async.receive_messages(max_message_count=2)

    assert len(received_messages) == 2
    assert all(
        key in normalize_application_properties(received_messages[0].application_properties)
        for key in trace_context_keys
    )
    assert all(
        key in normalize_application_properties(received_messages[1].application_properties)
        for key in trace_context_keys
    )
