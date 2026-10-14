"""Proposed IDM/ASM contract, transported by LP's existing event dispatcher."""

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar
from typing import Optional

from ddtrace.contrib._events.http import HttpRequestBaseEvent
from ddtrace.internal.core.events import event_field


class BodyAvailability(Enum):
    NOT_COLLECTED = "not_collected"


@dataclass(frozen=True)
class BlockResponse:
    status: int = 403
    headers: tuple[tuple[str, str], ...] = (("Content-Type", "text/plain"),)
    body: bytes = b"Blocked"


@dataclass
class BeforeHandlerEvent(HttpRequestBaseEvent):
    # Separate namespace prevents accidental registration with production listeners.
    event_name: ClassVar[str] = "scaffold.http.server.before_handler"
    operation_id: object = event_field()
    route: str = event_field()
    path_parameters: dict[str, object] = event_field()
    body_availability: BodyAvailability = event_field(default=BodyAvailability.NOT_COLLECTED)
    block_response: Optional[BlockResponse] = event_field(default=None)
