from typing import Callable

from ddtrace.propagation.http import HTTPPropagator


def extract_DD_context_from_messages(messages, extract_from_message: Callable):
    contexts = []
    for message in messages:
        context_json = extract_from_message(message)
        if context_json is not None:
            ctx = HTTPPropagator.extract(context_json)
            if ctx.trace_id is not None:
                contexts.append(ctx)
    return contexts
