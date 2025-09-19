import asyncio
import os
from threading import Event
from threading import Lock
from threading import Thread
from typing import List
from typing import Optional
from typing import Union
from uuid import uuid4

from azure.eventhub import EventData
from azure.eventhub import EventHubConsumerClient
from azure.eventhub import EventHubProducerClient
from azure.eventhub import PartitionContext
from azure.eventhub.aio import EventHubConsumerClient as EventHubConsumerClientAsync
from azure.eventhub.aio import EventHubProducerClient as EventHubProducerClientAsync
from azure.eventhub.aio import PartitionContext as PartitionContextAsync
from azure.eventhub.amqp import AmqpAnnotatedMessage
import pytest


CONNECTION_STRING = "Endpoint=sb://localhost;SharedAccessKeyName=RootManageSharedAccessKey;SharedAccessKey=SAS_KEY_VALUE;UseDevelopmentEmulator=true;"
EVENTHUB_NAME = "eh1"
CONSUMER_GROUP = "cg1"
DEFAULT_APPLICATION_PROPERTIES = {"property": "val", b"byteproperty": b"byteval"}
TRACE_CONTEXT_KEYS = [
    "x-datadog-trace-id",
    "x-datadog-parent-id",
    "x-datadog-sampling-priority",
    "x-datadog-tags",
    "traceparent",
    "tracestate",
]


def normalize_properties(event_data: Union[EventData, AmqpAnnotatedMessage]):
    if isinstance(event_data, EventData):
        props = event_data.properties
    elif isinstance(event_data, AmqpAnnotatedMessage):
        props = event_data.application_properties

    if not props:
        props = {}

    return {k.decode() if isinstance(k, bytes) else k: v for k, v in props.items()}


def make_events() -> List[Union[EventData, AmqpAnnotatedMessage]]:
    event_with_properties = EventData(body='{"body":"EventData with properties and custom message_id"}')
    event_with_properties.properties = DEFAULT_APPLICATION_PROPERTIES
    event_with_properties.message_id = str(uuid4())
    return [EventData(body='{"body":"EventData without properties"}'), event_with_properties]


def make_amqp_annotated_messages() -> List[Union[EventData, AmqpAnnotatedMessage]]:
    return [
        AmqpAnnotatedMessage(data_body='{"body":"AmqpAnnotatedMessage without properties"}'),
        AmqpAnnotatedMessage(
            data_body='{"body":"AmqpAnnotatedMessage with properties and custom message_id"}',
            application_properties=DEFAULT_APPLICATION_PROPERTIES,
            properties={"message_id": uuid4()},
        ),
    ]


def on_success(events: List[Union[EventData, AmqpAnnotatedMessage]], partition_id: Optional[str]):
    pass


def on_error(events: List[Union[EventData, AmqpAnnotatedMessage]], partition_id: Optional[str], error: Exception):
    raise error


async def on_success_async(events: List[Union[EventData, AmqpAnnotatedMessage]], partition_id: Optional[str]):
    pass


async def on_error_async(
    events: List[Union[EventData, AmqpAnnotatedMessage]], partition_id: Optional[str], error: Exception
):
    raise error


class EventHandler:
    def __init__(self):
        self.expected_event_count = 0
        self.received_events = []
        self._all_events_received = Event()
        self._consumer_ready = Event()
        self._expected_partition_count = 2
        self._lock = Lock()
        self._partitions_initialized_count = 0

    def on_event(self, partition_context: PartitionContext, event: Union[EventData, None]):
        self.received_events.append(event)
        if len(self.received_events) >= self.expected_event_count:
            self._all_events_received.set()

    def on_error(self, partition_context: PartitionContext, err):
        pass

    def on_partition_initialize(self, partition_context: PartitionContext):
        with self._lock:
            self._partitions_initialized_count += 1
            if self._partitions_initialized_count >= self._expected_partition_count:
                self._consumer_ready.set()

    def wait_util_ready(self, timeout: int):
        return self._consumer_ready.wait(timeout=timeout)

    def wait_for_events(self, timeout: int):
        return self._all_events_received.wait(timeout=timeout)


class EventHandlerAsync:
    def __init__(self):
        self.expected_event_count = 0
        self.received_events = []
        self._all_events_received = asyncio.Event()
        self._consumer_ready = asyncio.Event()
        self._expected_partition_count = 2
        self._partitions_initialized_count = 0

    async def on_event(self, partition_context: PartitionContextAsync, event: Union[EventData, None]) -> None:
        self.received_events.append(event)
        if len(self.received_events) >= self.expected_event_count:
            self._all_events_received.set()

    async def on_error(self, partition_context: PartitionContextAsync, err):
        pass

    async def on_partition_initialize(self, partition_context: PartitionContextAsync):
        self._partitions_initialized_count += 1
        if self._partitions_initialized_count >= self._expected_partition_count:
            self._consumer_ready.set()

    async def wait_util_ready(self, timeout: int):
        try:
            await asyncio.wait_for(self._consumer_ready.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def wait_for_events(self, timeout: int):
        try:
            await asyncio.wait_for(self._all_events_received.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False


def create_event_handler():
    client = EventHubConsumerClient.from_connection_string(
        conn_str=CONNECTION_STRING, eventhub_name=EVENTHUB_NAME, consumer_group=CONSUMER_GROUP
    )
    event_handler = EventHandler()

    thread = Thread(
        target=client.receive,
        kwargs={
            "on_event": event_handler.on_event,
            "on_error": event_handler.on_error,
            "on_partition_initialize": event_handler.on_partition_initialize,
            "starting_position": "@latest",
        },
    )

    thread.start()

    if not event_handler.wait_util_ready(timeout=5):
        raise TimeoutError("Event handler not ready within 5 seconds")

    return client, event_handler, thread


def close_event_handler(client: EventHubConsumerClient, thread: Thread):
    client.close()

    thread.join(timeout=5)
    if thread.is_alive():
        raise TimeoutError("Thread failed to terminate within 5 seconds")


async def create_event_handler_async():
    client = EventHubConsumerClientAsync.from_connection_string(
        conn_str=CONNECTION_STRING,
        eventhub_name=EVENTHUB_NAME,
        consumer_group=CONSUMER_GROUP,
    )

    event_handler = EventHandlerAsync()

    receive_task = asyncio.create_task(
        client.receive(
            on_event=event_handler.on_event,
            on_error=event_handler.on_error,
            on_partition_initialize=event_handler.on_partition_initialize,
            starting_position="@latest",
        )
    )

    ready = await event_handler.wait_util_ready(timeout=5)
    if not ready:
        raise TimeoutError("Event handler not ready within 5 seconds")

    return client, event_handler, receive_task


async def close_event_handler_async(client: EventHubConsumerClientAsync, receive_task: asyncio.Task):
    await client.close()

    receive_task.cancel()
    try:
        await receive_task
    except asyncio.CancelledError:
        pass


def run_test(
    producer_client: EventHubProducerClient,
    event_handler: EventHandler,
    method: Union[str, None],
    message_payload_type: Union[str, None],
    distributed_tracing_enabled: bool,
    batch_links_enabled: bool,
):
    events = make_events()
    amqp_annotated_messages = make_amqp_annotated_messages()

    event_length = len(events) + len(amqp_annotated_messages)
    event_handler.expected_event_count = event_length

    if method == "send_event" and message_payload_type == "single":
        for e in events:
            producer_client.send_event(e)
        for m in amqp_annotated_messages:
            producer_client.send_event(m)
    if method == "send_batch" and message_payload_type == "list":
        producer_client.send_batch(events)
        producer_client.send_batch(amqp_annotated_messages)
    elif method == "send_batch" and message_payload_type == "batch":
        event_batch = producer_client.create_batch()
        for e in events:
            event_batch.add(e)
        producer_client.send_batch(event_batch)

        amqp_annotated_message_batch = producer_client.create_batch()
        for m in amqp_annotated_messages:
            amqp_annotated_message_batch.add(m)
        producer_client.send_batch(amqp_annotated_message_batch)

    if not event_handler.wait_for_events(timeout=5):
        raise TimeoutError("Events were not received within 5 seconds")
    assert len(event_handler.received_events) == event_length

    if not distributed_tracing_enabled or not batch_links_enabled:
        assert not any(
            key in normalize_properties(e) for e in event_handler.received_events for key in TRACE_CONTEXT_KEYS
        )
    else:
        assert all(key in normalize_properties(e) for e in event_handler.received_events for key in TRACE_CONTEXT_KEYS)


async def run_test_async(
    producer_client: EventHubProducerClientAsync,
    event_handler: EventHandlerAsync,
    method: Union[str, None],
    message_payload_type: Union[str, None],
    distributed_tracing_enabled: bool,
    batch_links_enabled: bool,
):
    events = make_events()
    amqp_annotated_messages = make_amqp_annotated_messages()

    event_length = len(events) + len(amqp_annotated_messages)
    event_handler.expected_event_count = event_length

    if method == "send_event" and message_payload_type == "single":
        for e in events:
            await producer_client.send_event(e)
        for m in amqp_annotated_messages:
            await producer_client.send_event(m)
    if method == "send_batch" and message_payload_type == "list":
        await producer_client.send_batch(events)
        await producer_client.send_batch(amqp_annotated_messages)
    elif method == "send_batch" and message_payload_type == "batch":
        event_batch = await producer_client.create_batch()
        for e in events:
            event_batch.add(e)
        await producer_client.send_batch(event_batch)

        amqp_annotated_message_batch = await producer_client.create_batch()
        for m in amqp_annotated_messages:
            amqp_annotated_message_batch.add(m)
        await producer_client.send_batch(amqp_annotated_message_batch)

    if not await event_handler.wait_for_events(timeout=5):
        raise TimeoutError("Events were not received within 5 seconds")
    assert len(event_handler.received_events) == event_length

    if not distributed_tracing_enabled or not batch_links_enabled:
        assert not any(
            key in normalize_properties(e) for e in event_handler.received_events for key in TRACE_CONTEXT_KEYS
        )
    else:
        assert all(key in normalize_properties(e) for e in event_handler.received_events for key in TRACE_CONTEXT_KEYS)


@pytest.mark.asyncio
async def test_common():
    method = os.environ.get("METHOD")
    buffered_mode = os.environ.get("BUFFERED_MODE") == "True"
    is_async = os.environ.get("IS_ASYNC") == "True"
    message_payload_type = os.environ.get("MESSAGE_PAYLOAD_TYPE")
    distributed_tracing_enabled = os.environ.get("DD_AZURE_EVENTHUBS_DISTRIBUTED_TRACING", "True") == "True"
    batch_links_enabled = os.environ.get("DD_TRACE_AZURE_EVENTHUBS_BATCH_LINKS_ENABLED", "True") == "True"

    if is_async:
        producer_client = EventHubProducerClientAsync.from_connection_string(
            conn_str=CONNECTION_STRING,
            eventhub_name=EVENTHUB_NAME,
            buffered_mode=buffered_mode,
            on_error=on_error_async,
            on_success=on_success_async,
        )
        (consumer_client, event_handler, receive_task) = await create_event_handler_async()
        try:
            await run_test_async(
                producer_client,
                event_handler,
                method,
                message_payload_type,
                distributed_tracing_enabled,
                batch_links_enabled,
            )
        finally:
            await producer_client.close()
            await close_event_handler_async(consumer_client, receive_task)
    else:
        producer_client = EventHubProducerClient.from_connection_string(
            conn_str=CONNECTION_STRING,
            eventhub_name=EVENTHUB_NAME,
            buffered_mode=buffered_mode,
            on_error=on_error,
            on_success=on_success,
        )
        (consumer_client, event_handler, thread) = create_event_handler()
        try:
            run_test(
                producer_client,
                event_handler,
                method,
                message_payload_type,
                distributed_tracing_enabled,
                batch_links_enabled,
            )
        finally:
            producer_client.close()
            close_event_handler(consumer_client, thread)


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main(["-x", __file__]))
