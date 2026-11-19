import sys
from typing import Callable
from typing import Optional
from typing import Protocol
from typing import Union

from ddtrace.internal import core
from ddtrace.internal._context_watcher import PYTHON_CONTEXT_SWITCH_EVENT
from ddtrace.internal._context_watcher import register_context_watcher
from ddtrace.internal.native._native import Context
from ddtrace.internal.native._native import SpanData
from ddtrace.internal.settings._config import config


class BaseContextProviderProtocol(Protocol):
    """Structural stand-in for ddtrace._trace.provider.BaseContextProvider, so this module does not need to
    import from the tracing product.
    """

    def active(self) -> Optional[Union[Context, SpanData]]: ...


class TracerProtocol(Protocol):
    @property
    def context_provider(self) -> BaseContextProviderProtocol: ...


_ContextActivationListener = Callable[[BaseContextProviderProtocol, Optional[Union[Context, SpanData]]], None]
_ContextSwitchListener = Callable[[], None]
_ThreadContextListeners = tuple[_ContextActivationListener, _ContextSwitchListener]


if sys.platform == "linux":
    from ddtrace.internal.native._native import sync_otel_thread_context

    def register_otel_thread_context_listener(tracer: TracerProtocol) -> Optional[_ThreadContextListeners]:
        if not config._otel_thread_context_enabled:
            return None

        def _sync_active_otel_thread_context() -> None:
            sync_otel_thread_context(tracer.context_provider.active())

        def _on_context_provider_activate(
            provider: BaseContextProviderProtocol, ctx: Optional[Union[Context, SpanData]]
        ) -> None:
            if provider is tracer.context_provider:
                sync_otel_thread_context(ctx)

        register_context_watcher()
        core.on("ddtrace.context_provider.activate", _on_context_provider_activate)
        core.on(PYTHON_CONTEXT_SWITCH_EVENT, _sync_active_otel_thread_context)
        return _on_context_provider_activate, _sync_active_otel_thread_context

else:

    def register_otel_thread_context_listener(tracer: TracerProtocol) -> Optional[_ThreadContextListeners]:
        return None
