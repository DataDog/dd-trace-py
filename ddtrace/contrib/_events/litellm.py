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
    """One LiteLLM completion call, for ContextSubscriber subscribers. on_ended does not run when
    on_started raises, and must not block a stream: block in on_started.
    """

    event_name = LiteLLMEvents.CALL.value

    operation: str = event_field()
    request_args: tuple[Any, ...] = event_field(default=())
    request_kwargs: dict[str, Any] = event_field(default_factory=dict)
    stream: bool = event_field(default=False)
    response: Optional[Any] = event_field(default=None)
