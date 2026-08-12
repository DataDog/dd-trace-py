import sys
from typing import Callable
from typing import Optional
from typing import Protocol
from typing import Union

from ddtrace._trace.context import Context
from ddtrace._trace.provider import BaseContextProvider
from ddtrace._trace.span import Span
from ddtrace.internal import core
from ddtrace.internal.settings._config import config


class TracerProtocol(Protocol):
    @property
    def context_provider(self) -> BaseContextProvider: ...


_ContextActivationListener = Callable[[BaseContextProvider, Optional[Union[Context, Span]]], None]


if sys.platform == "linux":
    from ddtrace.internal.native._native import detach_otel_thread_context
    from ddtrace.internal.native._native import update_otel_thread_context

    def register_otel_thread_context_listener(tracer: TracerProtocol) -> Optional[_ContextActivationListener]:
        if not config._otel_thread_context_enabled:
            return None

        def _sync_otel_thread_context(provider: BaseContextProvider, ctx: Optional[Union[Context, Span]]) -> None:
            if provider is not tracer.context_provider:
                return

            if type(ctx) is Span:
                sampling_priority = ctx._local_root.context.sampling_priority
                trace_flags = 1 if sampling_priority is not None and sampling_priority > 0 else 0
                update_otel_thread_context(ctx, ctx._local_root_value, trace_flags)
            else:
                detach_otel_thread_context()

        core.on("ddtrace.context_provider.activate", _sync_otel_thread_context)
        return _sync_otel_thread_context

else:

    def register_otel_thread_context_listener(tracer: TracerProtocol) -> Optional[_ContextActivationListener]:
        return None
