from typing import Optional

from ddtrace._trace.context import Context
from ddtrace._trace.provider import ActiveTrace
from ddtrace._trace.provider import DefaultContextProvider


def test_context_provider_activation_listener() -> None:
    provider = DefaultContextProvider()
    activated_contexts: list[Optional[ActiveTrace]] = []

    def listener(ctx: Optional[ActiveTrace]) -> None:
        activated_contexts.append(ctx)

    provider._add_activation_listener(listener)
    context = Context()
    provider.activate(context)
    provider._remove_activation_listener(listener)
    provider.activate(None)

    assert activated_contexts == [context]
