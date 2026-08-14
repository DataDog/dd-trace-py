import sys
from typing import Callable
from typing import Optional
from typing import Protocol
from typing import Union

from ddtrace._trace.context import SAMPLING_DECISION_EVENT
from ddtrace._trace.context import Context
from ddtrace._trace.provider import BaseContextProvider
from ddtrace._trace.span import Span
from ddtrace.internal import core
from ddtrace.internal.compat import NumericType
from ddtrace.internal.settings._config import config


class TracerProtocol(Protocol):
    @property
    def context_provider(self) -> BaseContextProvider: ...


_ActiveTrace = Optional[Union[Context, Span]]
_ContextActivationListener = Callable[[BaseContextProvider, _ActiveTrace], None]
_ResyncListener = Callable[[], None]
_ThreadContextListeners = tuple[_ContextActivationListener, _ResyncListener]


def _w3c_trace_flags(sampling_priority: Optional[NumericType]) -> int:
    return 1 if sampling_priority is not None and sampling_priority > 0 else 0


if sys.platform == "linux":
    from ddtrace.internal.native._native import detach_otel_thread_context
    from ddtrace.internal.native._native import update_otel_thread_context
    from ddtrace.internal.native._native import update_otel_thread_context_ids

    def register_otel_thread_context_listener(tracer: TracerProtocol) -> Optional[_ThreadContextListeners]:
        if not config._otel_thread_context_enabled:
            return None

        def _sync_otel_thread_context(ctx: _ActiveTrace) -> None:
            if type(ctx) is Span:
                update_otel_thread_context(
                    ctx, ctx._local_root_value, _w3c_trace_flags(ctx._local_root.context.sampling_priority)
                )
            elif type(ctx) is Context and ctx.trace_id is not None and ctx.span_id is not None:
                # A Context is a span this execution runs inside but does not own -- a
                # remote parent, or the submitter of work handed to this thread. The
                # execution is still attributable to it. Its local root is not knowable
                # from here, so the span stands in for it, as it does for a root span.
                update_otel_thread_context_ids(
                    ctx.trace_id, ctx.span_id, _w3c_trace_flags(ctx.sampling_priority), ctx.span_id
                )
            else:
                detach_otel_thread_context()

        def _sync_active_otel_thread_context() -> None:
            _sync_otel_thread_context(tracer.context_provider._peek_active())

        def _on_context_provider_activate(provider: BaseContextProvider, ctx: _ActiveTrace) -> None:
            if provider is tracer.context_provider:
                _sync_otel_thread_context(ctx)

        core.on("ddtrace.context_provider.activate", _on_context_provider_activate)
        core.on("python.context.switch", _sync_active_otel_thread_context)
        # Sampling usually decides at trace-chunk finish, after the record was published
        # with trace_flags=0.
        core.on(SAMPLING_DECISION_EVENT, _sync_active_otel_thread_context)

        if sys.implementation.name == "cpython" and sys.version_info >= (3, 14):
            from ddtrace.internal.native._native import register_context_watcher

            register_context_watcher()
        return _on_context_provider_activate, _sync_active_otel_thread_context

else:

    def register_otel_thread_context_listener(tracer: TracerProtocol) -> Optional[_ThreadContextListeners]:
        return None
