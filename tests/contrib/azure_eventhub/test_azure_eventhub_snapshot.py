import asyncio
from threading import Event
from threading import Lock
from threading import Thread

from azure.eventhub import EventData
from azure.eventhub import EventHubConsumerClient
from azure.eventhub import EventHubProducerClient
from azure.eventhub.aio import EventHubConsumerClient as EventHubConsumerClientAsync
from azure.eventhub.aio import EventHubProducerClient as EventHubProducerClientAsync
from azure.eventhub.amqp import AmqpAnnotatedMessage
import pytest

from ddtrace.contrib.internal.azure_eventhub.patch import patch
from ddtrace.contrib.internal.azure_eventhub.patch import unpatch


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

DISTRIBUTED_TRACING_DISABLED_PARAMS = {
    "DD_AZURE_EVENTHUB_DISTRIBUTED_TRACING": "False",
}

SPAN_ATTRIBUTE_SCHEMA_V1_PARAMS = {"DD_TRACE_SPAN_ATTRIBUTE_SCHEMA": "v1"}


class EventHandler:
    def __init__(self, expected_event_count):
        self.received_events = []
        self._all_events_received = Event()
        self._consumer_ready = Event()
        self._expected_event_count = expected_event_count
        self._expected_partition_count = 2
        self._lock = Lock()
        self._partitions_initialized_count = 0

    def on_event(self, partition_context, event):
        self.received_events.append(event)
        if len(self.received_events) >= self._expected_event_count:
            self._all_events_received.set()

    def on_error(self, partition_context, err):
        pass

    def on_partition_initialize(self, partition_context):
        with self._lock:
            self._partitions_initialized_count += 1
            if self._partitions_initialized_count >= self._expected_partition_count:
                self._consumer_ready.set()

    def wait_util_ready(self, timeout):
        return self._consumer_ready.wait(timeout=timeout)

    def wait_for_events(self, timeout):
        return self._all_events_received.wait(timeout=timeout)


class EventHandlerAsync:
    def __init__(self, expected_event_count):
        self.received_events = []
        self._all_events_received = asyncio.Event()
        self._consumer_ready = asyncio.Event()
        self._expected_event_count = expected_event_count
        self._expected_partition_count = 2
        self._partitions_initialized_count = 0

    async def on_event(self, partition_context, event):
        self.received_events.append(event)
        if len(self.received_events) >= self._expected_event_count:
            self._all_events_received.set()

    async def on_error(self, partition_context, err):
        pass

    async def on_partition_initialize(self, partition_context):
        self._partitions_initialized_count += 1
        if self._partitions_initialized_count >= self._expected_partition_count:
            self._consumer_ready.set()

    async def wait_util_ready(self, timeout):
        try:
            await asyncio.wait_for(self._consumer_ready.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def wait_for_events(self, timeout):
        try:
            await asyncio.wait_for(self._all_events_received.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False


def normalize_properties(event_data: EventData | AmqpAnnotatedMessage):
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


@pytest.fixture(autouse=True)
def patch_azure_eventhub():
    patch()
    yield
    unpatch()


@pytest.fixture()
def azure_eventhub_producer_client():
    with EventHubProducerClient.from_connection_string(
        conn_str=CONNECTION_STRING, eventhub_name=EVENTHUB_NAME
    ) as eventhub_producer_client:
        yield eventhub_producer_client


@pytest.fixture()
async def azure_eventhub_producer_client_async():
    async with EventHubProducerClientAsync.from_connection_string(
        conn_str=CONNECTION_STRING, eventhub_name=EVENTHUB_NAME
    ) as eventhub_producer_client:
        yield eventhub_producer_client


@pytest.fixture()
def azure_eventhub_event_handler(request):
    client = EventHubConsumerClient.from_connection_string(
        conn_str=CONNECTION_STRING, eventhub_name=EVENTHUB_NAME, consumer_group=CONSUMER_GROUP
    )

    event_handler = EventHandler(expected_event_count=getattr(request, "param", 1))

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

    try:
        if not event_handler.wait_util_ready(timeout=5):
            raise TimeoutError("Event handler not ready within 5 seconds")

        yield event_handler

    finally:
        client.close()

        thread.join(timeout=5)
        if thread.is_alive():
            raise TimeoutError("Thread failed to terminate within 5 seconds")


@pytest.fixture()
async def azure_eventhub_event_handler_async(request):
    client = EventHubConsumerClientAsync.from_connection_string(
        conn_str=CONNECTION_STRING,
        eventhub_name=EVENTHUB_NAME,
        consumer_group=CONSUMER_GROUP,
    )

    event_handler = EventHandlerAsync(expected_event_count=getattr(request, "param", 1))

    receive_task = asyncio.create_task(
        client.receive(
            on_event=event_handler.on_event,
            on_error=event_handler.on_error,
            on_partition_initialize=event_handler.on_partition_initialize,
            starting_position="@latest",
        )
    )

    try:
        ready = await event_handler.wait_util_ready(timeout=5)
        if not ready:
            raise TimeoutError("Event handler not ready within 5 seconds")

        yield event_handler
    finally:
        await client.close()

        receive_task.cancel()
        try:
            await receive_task
        except asyncio.CancelledError:
            pass


@pytest.mark.parametrize("azure_eventhub_event_handler", [4], indirect=True)
@pytest.mark.snapshot
def test_send_event(azure_eventhub_producer_client: EventHubProducerClient, azure_eventhub_event_handler: EventHandler):
    events = make_events()

    for event in events:
        azure_eventhub_producer_client.send_event(event)

    if not azure_eventhub_event_handler.wait_for_events(timeout=5):
        raise TimeoutError("Events were not received within 5 seconds")

    assert len(azure_eventhub_event_handler.received_events) == len(events)
    assert all(
        key in normalize_properties(event)
        for event in azure_eventhub_event_handler.received_events
        for key in TRACE_CONTEXT_KEYS
    )


@pytest.mark.parametrize("azure_eventhub_event_handler", [4], indirect=True)
@pytest.mark.snapshot
def test_send_batch(azure_eventhub_producer_client: EventHubProducerClient, azure_eventhub_event_handler: EventHandler):
    events = make_events()
    azure_eventhub_producer_client.send_batch(events)

    if not azure_eventhub_event_handler.wait_for_events(timeout=5):
        raise TimeoutError("Events were not received within 5 seconds")

    assert len(azure_eventhub_event_handler.received_events) == len(events)
    assert all(
        key in normalize_properties(event)
        for event in azure_eventhub_event_handler.received_events
        for key in TRACE_CONTEXT_KEYS
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("azure_eventhub_event_handler_async", [4], indirect=True)
@pytest.mark.snapshot
async def test_send_event_async(
    azure_eventhub_producer_client_async: EventHubProducerClientAsync,
    azure_eventhub_event_handler_async: EventHandlerAsync,
):
    events = make_events()

    for event in events:
        await azure_eventhub_producer_client_async.send_event(event)

    if not await azure_eventhub_event_handler_async.wait_for_events(timeout=5):
        raise TimeoutError("Events were not received within 5 seconds")

    assert len(azure_eventhub_event_handler_async.received_events) == len(events)
    assert all(
        key in normalize_properties(event)
        for event in azure_eventhub_event_handler_async.received_events
        for key in TRACE_CONTEXT_KEYS
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("azure_eventhub_event_handler_async", [4], indirect=True)
@pytest.mark.snapshot
async def test_send_batch_async(
    azure_eventhub_producer_client_async: EventHubProducerClientAsync,
    azure_eventhub_event_handler_async: EventHandlerAsync,
):
    events = make_events()
    await azure_eventhub_producer_client_async.send_batch(events)

    if not await azure_eventhub_event_handler_async.wait_for_events(timeout=5):
        raise TimeoutError("Events were not received within 5 seconds")

    assert len(azure_eventhub_event_handler_async.received_events) == len(events)
    assert all(
        key in normalize_properties(event)
        for event in azure_eventhub_event_handler_async.received_events
        for key in TRACE_CONTEXT_KEYS
    )
