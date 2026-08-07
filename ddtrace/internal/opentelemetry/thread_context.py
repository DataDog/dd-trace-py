import sys
from typing import Optional
from typing import Protocol
from typing import Union

from ddtrace._trace.context import Context
from ddtrace._trace.provider import BaseContextProvider
from ddtrace._trace.span import Span
from ddtrace.internal import core
from ddtrace.internal.context_watcher import register_context_watcher


class TracerProtocol(Protocol):
    @property
    def context_provider(self) -> BaseContextProvider: ...


if sys.platform == "linux":
    from ddtrace.internal.native._native import detach_otel_thread_context
    from ddtrace.internal.native._native import update_otel_thread_context

    def register_otel_thread_context_listener(tracer: TracerProtocol) -> None:
        def _sync_otel_thread_context(ctx: Optional[Union[Context, Span]]) -> None:
            if type(ctx) is Span:
                update_otel_thread_context(ctx, ctx._local_root_value)
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
        core.on("python.context.switch", _sync_active_otel_thread_context)
        # Keep this listener installed when native watching is unavailable:
        # greenlet and the compatibility integrations publish the same event.
        register_context_watcher()

else:

    def register_otel_thread_context_listener(tracer: TracerProtocol) -> None:
        pass
