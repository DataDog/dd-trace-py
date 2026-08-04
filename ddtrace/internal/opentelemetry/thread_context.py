import sys
from typing import Callable
from typing import Optional
from typing import Protocol
from typing import Union

from ddtrace._trace.context import Context
from ddtrace._trace.provider import BaseContextProvider
from ddtrace._trace.span import Span
from ddtrace.internal import core
from ddtrace.internal.constants import PYTHON_CONTEXT_SWITCH_EVENT
from ddtrace.internal.settings._config import config


class TracerProtocol(Protocol):
    @property
    def context_provider(self) -> BaseContextProvider: ...


_ContextActivationListener = Callable[[BaseContextProvider, Optional[Union[Context, Span]]], None]
_ContextSwitchListener = Callable[[], None]
_ThreadContextListeners = tuple[_ContextActivationListener, _ContextSwitchListener]


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
            # active() is the provider contract and also discards finished
            # spans before their context is published to native thread-local storage.
            _sync_otel_thread_context(tracer.context_provider.active())

        def _on_context_provider_activate(provider: BaseContextProvider, ctx: Optional[Union[Context, Span]]) -> None:
            if provider is tracer.context_provider:
                _sync_otel_thread_context(ctx)

        core.on("ddtrace.context_provider.activate", _on_context_provider_activate)
        core.on(PYTHON_CONTEXT_SWITCH_EVENT, _sync_active_otel_thread_context)

        if sys.implementation.name == "cpython" and sys.version_info >= (3, 14):
            from ddtrace.internal.native._native import register_context_watcher

            register_context_watcher()
        return _on_context_provider_activate, _sync_active_otel_thread_context

else:

    def register_otel_thread_context_listener(tracer: TracerProtocol) -> Optional[_ThreadContextListeners]:
        return None
