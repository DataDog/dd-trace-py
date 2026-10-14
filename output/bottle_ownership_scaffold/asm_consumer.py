"""ASM: an injected inspection policy, with no Bottle dependency or WAF wiring."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from typing import Optional

from ddtrace.internal import core

from .contracts import BeforeHandlerEvent
from .contracts import BlockResponse
from .contracts import BodyAvailability


@dataclass(frozen=True)
class RequestFacts:
    operation_id: object
    method: str
    url: str
    headers: tuple[tuple[str, str], ...]
    query: Optional[str]
    route: str
    path_parameters: tuple[tuple[str, object], ...]
    body_availability: BodyAvailability


class AsmConsumer:
    def __init__(self, inspect: Callable[[RequestFacts], Optional[BlockResponse]]):
        self.inspect = inspect

    def _on_before_handler(self, event: BeforeHandlerEvent) -> None:
        decision = self.inspect(
            RequestFacts(
                operation_id=event.operation_id,
                method=event.request_method,
                url=event.request_url,
                headers=tuple(event.request_headers.items()),
                query=event.query,
                route=event.route,
                path_parameters=tuple(event.path_parameters.items()),
                body_availability=event.body_availability,
            )
        )
        if decision is not None and not isinstance(decision, BlockResponse):
            raise TypeError("Inspector must return None or BlockResponse")
        event.block_response = decision

    def __enter__(self) -> "AsmConsumer":
        # This scaffold supports one decision authority; registration is startup-only.
        if core.has_listeners(BeforeHandlerEvent.event_name):
            raise RuntimeError("A scaffold decision consumer is already registered")
        core.on(BeforeHandlerEvent.event_name, self._on_before_handler)
        return self

    def __exit__(self, *exc: Any) -> None:
        core.reset_listeners(BeforeHandlerEvent.event_name, self._on_before_handler)
