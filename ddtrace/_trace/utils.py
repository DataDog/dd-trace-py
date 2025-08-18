from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import List
from typing import Optional

from ddtrace.propagation.http import HTTPPropagator


if TYPE_CHECKING:
    from ddtrace._trace.context import Context


def extract_DD_context_from_messages(messages: List[Any], extract_from_message: Callable) -> Optional["Context"]:
    ctx = None
    if len(messages) >= 1:
        message = messages[0]
        context_json = extract_from_message(message)
        if context_json is not None:
            child_of = HTTPPropagator.extract(context_json)
            if child_of.trace_id is not None:
                ctx = child_of
    return ctx
