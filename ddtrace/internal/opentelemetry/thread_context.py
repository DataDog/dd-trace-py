import sys
from typing import Optional
from typing import Protocol
from typing import Union

from ddtrace._trace.context import Context
from ddtrace._trace.provider import BaseContextProvider
from ddtrace._trace.span import Span
from ddtrace.internal import core


class TracerProtocol(Protocol):
    @property
    def context_provider(self) -> BaseContextProvider: ...


if sys.platform == "linux":
    from ddtrace.internal.native._native import detach_otel_thread_context
    from ddtrace.internal.native._native import update_otel_thread_context

    def register_otel_thread_context_listener(tracer: TracerProtocol) -> None:
        def _sync_otel_thread_context(provider: BaseContextProvider, ctx: Optional[Union[Context, Span]]) -> None:
            if provider is not tracer.context_provider:
                return

            if type(ctx) is Span:
                update_otel_thread_context(ctx, ctx._local_root_value)
            else:
                detach_otel_thread_context()

        core.on("ddtrace.context_provider.activate", _sync_otel_thread_context)

else:

    def register_otel_thread_context_listener(tracer: TracerProtocol) -> None:
        pass
