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


class _HttpClientResponse(Protocol):
    @property
    def headers(self) -> MutableMapping[str, str]: ...

    @property
    def status_code(self) -> int: ...

    def json(self) -> JsonType: ...


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

    def set_response(self, response: _HttpClientResponse) -> None:
        self.response_status_code = response.status_code
        self.response_headers = response.headers


@dataclass
class HttpRequestBaseEvent(HttpBaseEvent):
    """Base event for traced HTTP requests."""

    http_operation: str = event_field()
    query: str = event_field()
