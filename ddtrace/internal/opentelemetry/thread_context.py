import sys
from typing import Callable
from typing import Optional
from typing import Protocol
from typing import Union

from ddtrace._trace.context import SAMPLING_DECISION_EVENT
from ddtrace._trace.context import Context
from ddtrace._trace.context import enable_sampling_decision_event
from ddtrace._trace.provider import BaseContextProvider
from ddtrace._trace.span import Span
from ddtrace.internal import core
from ddtrace.internal.settings._config import config


class TracerProtocol(Protocol):
    @property
    def context_provider(self) -> BaseContextProvider: ...


_ContextActivationListener = Callable[[BaseContextProvider, Optional[Union[Context, Span]]], None]
# Registered for every event that invalidates the record without carrying the new value.
_ResyncListener = Callable[[], None]
_ThreadContextListeners = tuple[_ContextActivationListener, _ResyncListener]


if sys.platform == "linux":
    from ddtrace.internal.native._native import detach_otel_thread_context
    from ddtrace.internal.native._native import update_otel_thread_context

    def register_otel_thread_context_listener(tracer: TracerProtocol) -> Optional[_ThreadContextListeners]:
        if not config._otel_thread_context_enabled:
            return None

        def _sync_otel_thread_context(ctx: Optional[Union[Context, Span]]) -> None:
            if type(ctx) is Span:
                sampling_priority = ctx._local_root.context.sampling_priority
                trace_flags = 1 if sampling_priority is not None and sampling_priority > 0 else 0
                update_otel_thread_context(ctx, ctx._local_root_value, trace_flags)
            else:
                detach_otel_thread_context()

        def _sync_active_otel_thread_context() -> None:
            _sync_otel_thread_context(tracer.context_provider.active())

        def _on_context_provider_activate(provider: BaseContextProvider, ctx: Optional[Union[Context, Span]]) -> None:
            if provider is tracer.context_provider:
                _sync_otel_thread_context(ctx)

        core.on("ddtrace.context_provider.activate", _on_context_provider_activate)
        core.on("python.context.switch", _sync_active_otel_thread_context)
        # Sampling usually decides at trace-chunk finish, after the record was published
        # with trace_flags=0.
        core.on(SAMPLING_DECISION_EVENT, _sync_active_otel_thread_context)
        enable_sampling_decision_event()

        if sys.implementation.name == "cpython" and sys.version_info >= (3, 14):
            from ddtrace.internal.native._native import register_context_watcher

            register_context_watcher()
        return _on_context_provider_activate, _sync_active_otel_thread_context

else:

    def register_otel_thread_context_listener(tracer: TracerProtocol) -> Optional[_ThreadContextListeners]:
        return None
