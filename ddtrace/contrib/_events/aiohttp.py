from dataclasses import dataclass
from enum import Enum

from ddtrace._trace.events import TracingEvent
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.core.events import event_field


class AIOHttpEvents(Enum):
    CONNECTOR_CONNECT = "aiohttp.client.connector.connect"
    CONNECTOR_CREATE_CONNECTION = "aiohttp.client.connector.create_connection"


@dataclass
class AIOHttpConnectEvent(TracingEvent):
    event_name = AIOHttpEvents.CONNECTOR_CONNECT.value

    span_kind = SpanKind.CLIENT
    span_type = SpanTypes.HTTP

    connector_class_name: str = event_field()

    def __post_init__(self):
        self.span_name = f"{self.connector_class_name}.connect"


@dataclass
class AIOHttpCreateConnectEvent(TracingEvent):
    event_name = AIOHttpEvents.CONNECTOR_CREATE_CONNECTION.value

    span_kind = SpanKind.CLIENT
    span_type = SpanTypes.HTTP

    connector_class_name: str = event_field()

    def __post_init__(self):
        self.span_name = f"{self.connector_class_name}._create_connection"
