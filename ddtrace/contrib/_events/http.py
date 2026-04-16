from dataclasses import dataclass
from typing import Mapping
from typing import MutableMapping
from typing import Optional
from typing import Protocol
from typing import Sequence
from typing import Union

from ddtrace.internal.core.events import Event
from ddtrace.internal.core.events import event_field


JsonType = Union[None, bool, int, float, str, Sequence["JsonType"], Mapping[str, "JsonType"]]


class _HttpBaseResponse(Protocol):
    @property
    def headers(self) -> MutableMapping[str, str]: ...

    def json(self) -> JsonType: ...


class _HttpResponseWithStatusCode(_HttpBaseResponse, Protocol):
    @property
    def status_code(self) -> int: ...


class _HttpResponseWithStatus(_HttpBaseResponse, Protocol):
    @property
    def status(self) -> int: ...


_HttpResponse = Union[_HttpResponseWithStatus, _HttpResponseWithStatusCode]


@dataclass
class HttpBaseEvent(Event):
    """Common HTTP request/response data.

    This event contains the information needed by both AppSec and APM events when tracing
    http calls (whether client or server).
    """

    request_url: str = event_field()
    request_method: str = event_field()
    request_headers: MutableMapping[str, str] = event_field()

    response_headers: Mapping[str, str] = event_field(default_factory=dict)
    response_status_code: Optional[int] = event_field(default=None)

    def set_response(self, response: _HttpResponse) -> None:
        self.response_status_code = getattr(response, "status_code", None)
        if self.response_status_code is None:
            self.response_status_code = getattr(response, "status", None)
        self.response_headers = response.headers


@dataclass
class HttpRequestBaseEvent(HttpBaseEvent):
    """Base event for traced HTTP requests."""

    http_operation: str = event_field()
    query: str = event_field()
