from dataclasses import dataclass
from enum import Enum

from ddtrace.contrib._events.http_client import HttpClientRequestEvent


class HttpxEvents(Enum):
    HTTPX_REQUEST = "httpx.request"


@dataclass
class HttpxRequestEvent(HttpClientRequestEvent):
    event_name = HttpxEvents.HTTPX_REQUEST.value
