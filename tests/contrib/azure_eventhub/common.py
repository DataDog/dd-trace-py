import asyncio
import os
from threading import Event
from threading import Lock
from threading import Thread
from typing import List
from typing import Union

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


def make_events():
    event_with_properties = EventData("EventData with properties")
    event_with_properties.properties = DEFAULT_APPLICATION_PROPERTIES

    return [
        EventData("EventData without properties"),
        event_with_properties,
        AmqpAnnotatedMessage(data_body="AmqpAnnotatedMessage without properties"),
        AmqpAnnotatedMessage(
            data_body="AmqpAnnotatedMessage with properties",
            application_properties=DEFAULT_APPLICATION_PROPERTIES,
        ),
    ]


class EventHandler:
    def __init__(self, expected_event_count: int):
        self.received_events = []
        self._all_events_received = Event()
        self._consumer_ready = Event()
        self._expected_event_count = expected_event_count
        self._expected_partition_count = 2
        self._lock = Lock()
        self._partitions_initialized_count = 0

    def on_event(self, partition_context: PartitionContext, event: Union[EventData, None]):
        self.received_events.append(event)
        if len(self.received_events) >= self._expected_event_count:
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
    def __init__(self, expected_event_count: int):
        self.received_events = []
        self._all_events_received = asyncio.Event()
        self._consumer_ready = asyncio.Event()
        self._expected_event_count = expected_event_count
        self._expected_partition_count = 2
        self._partitions_initialized_count = 0

    async def on_event(self, partition_context: PartitionContextAsync, event: Union[EventData, None]) -> None:
        self.received_events.append(event)
        if len(self.received_events) >= self._expected_event_count:
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


def create_eventhub_event_handler(expected_event_count: int):
    client = EventHubConsumerClient.from_connection_string(
        conn_str=CONNECTION_STRING, eventhub_name=EVENTHUB_NAME, consumer_group=CONSUMER_GROUP
    )
    event_handler = EventHandler(expected_event_count=expected_event_count)

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


def close_eventhub_event_handler(client: EventHubConsumerClient, thread: Thread):
    client.close()

    thread.join(timeout=5)
    if thread.is_alive():
        raise TimeoutError("Thread failed to terminate within 5 seconds")


async def crate_azure_eventhub_event_handler_async(expected_event_count: int):
    client = EventHubConsumerClientAsync.from_connection_string(
        conn_str=CONNECTION_STRING,
        eventhub_name=EVENTHUB_NAME,
        consumer_group=CONSUMER_GROUP,
    )

    event_handler = EventHandlerAsync(expected_event_count=expected_event_count)

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


async def close_eventhub_event_handler_async(client: EventHubConsumerClientAsync, receive_task: asyncio.Task):
    await client.close()

    receive_task.cancel()
    try:
        await receive_task
    except asyncio.CancelledError:
        pass


def run_test(
    eventhub_producer_client: EventHubProducerClient,
    eventhub_event_handler: EventHandler,
    events: List[Union[EventData, AmqpAnnotatedMessage]],
    is_batch: bool,
    distributed_tracing_enabled: bool,
):
    if is_batch:
        eventhub_producer_client.send_batch(events)
    else:
        for e in events:
            eventhub_producer_client.send_event(e)

    if not eventhub_event_handler.wait_for_events(timeout=5):
        raise TimeoutError("Events were not received within 5 seconds")

    assert len(eventhub_event_handler.received_events) == len(events)
    if distributed_tracing_enabled:
        assert all(
            key in normalize_properties(e) for e in eventhub_event_handler.received_events for key in TRACE_CONTEXT_KEYS
        )
    else:
        assert not any(
            key in normalize_properties(e) for e in eventhub_event_handler.received_events for key in TRACE_CONTEXT_KEYS
        )


async def run_test_async(
    eventhub_producer_client_async: EventHubProducerClientAsync,
    eventhub_event_handler_async: EventHandlerAsync,
    events: List[Union[EventData, AmqpAnnotatedMessage]],
    is_batch: bool,
    distributed_tracing_enabled: bool,
):
    if is_batch:
        await eventhub_producer_client_async.send_batch(events)
    else:
        for e in events:
            await eventhub_producer_client_async.send_event(e)

    if not await eventhub_event_handler_async.wait_for_events(timeout=5):
        raise TimeoutError("Events were not received within 5 seconds")

    assert len(eventhub_event_handler_async.received_events) == len(events)

    if distributed_tracing_enabled:
        assert all(
            key in normalize_properties(e)
            for e in eventhub_event_handler_async.received_events
            for key in TRACE_CONTEXT_KEYS
        )
    else:
        assert not any(
            key in normalize_properties(e)
            for e in eventhub_event_handler_async.received_events
            for key in TRACE_CONTEXT_KEYS
        )


@pytest.mark.asyncio
async def test_common():
    is_async = os.environ.get("IS_ASYNC", "False") == "True"
    is_batch = os.environ.get("IS_BATCH", "False") == "True"
    distributed_tracing_enabled = os.environ.get("DD_AZURE_EVENTHUB_DISTRIBUTED_TRACING", "False") == "True"

    events = make_events()
    event_count = len(events)

    if is_async:
        async with EventHubProducerClientAsync.from_connection_string(
            conn_str=CONNECTION_STRING, eventhub_name=EVENTHUB_NAME
        ) as eventhub_producer_client_async:
            (
                eventhub_consumer_client_async,
                event_handler_async,
                receive_task,
            ) = await crate_azure_eventhub_event_handler_async(event_count)
            try:
                await run_test_async(
                    eventhub_producer_client_async, event_handler_async, events, is_batch, distributed_tracing_enabled
                )
            finally:
                await close_eventhub_event_handler_async(eventhub_consumer_client_async, receive_task)
    else:
        with EventHubProducerClient.from_connection_string(
            conn_str=CONNECTION_STRING, eventhub_name=EVENTHUB_NAME
        ) as eventhub_producer_client:
            eventhub_consumer_client, event_handler, thread = create_eventhub_event_handler(event_count)
            try:
                run_test(eventhub_producer_client, event_handler, events, is_batch, distributed_tracing_enabled)
            finally:
                close_eventhub_event_handler(eventhub_consumer_client, thread)


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main(["-x", __file__]))
