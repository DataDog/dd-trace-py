"""Lifecycle event for a LiteLLM completion call (completion, acompletion, text_completion,
atext_completion), so products can subscribe without the contrib importing them.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any
from typing import Optional

from ddtrace.internal.core.events import Event
from ddtrace.internal.core.events import event_field


class LiteLLMEvents(Enum):
    CALL = "litellm.call"


@dataclass
class LiteLLMCallEvent(Event):
    """Lifecycle event for one LiteLLM completion call.

    Used with core.context_with_event inside the request context so products can subscribe
    through ContextSubscriber[LiteLLMCallEvent]:
    - on_started(ctx) runs before the provider call and may raise a DDBlockException to stop it.
    - on_ended(ctx, exc_info) runs when the call returns or raises. For a non-streamed call that
      succeeded, ctx.event.response holds the response; for a streamed call, ctx.event.stream is
      True and ctx.event.response is None (the answer arrives later, chunk by chunk).
    - on_ended does not run when on_started raises: a subscriber that blocks cleans up before raising.
    - For a streamed call, on_ended runs once the stream is created and must not block, since the
      caller would get no stream to close: block a stream in on_started.

    Subscribers keep per-call state on the context (ctx.set_item / ctx.get_item).
    """

    event_name = LiteLLMEvents.CALL.value

    operation: str = event_field()
    request_args: tuple[Any, ...] = event_field(default=())
    request_kwargs: dict[str, Any] = event_field(default_factory=dict)
    stream: bool = event_field(default=False)
    response: Optional[Any] = event_field(default=None)
