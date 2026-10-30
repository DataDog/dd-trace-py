"""Draft only: proposed Flask request consolidation, never imported by instrumentation.

DeferredRequestRunner describes an extension that is NOT implemented by the current
run_web_framework_request. This file shows the intended boundary and subscriber;
it is not a second installed Flask integration. See the accompanying RST proposal
for the remaining compatibility work and deletions required before activation.
"""

from collections.abc import Callable
from collections.abc import Iterable
import sys
from types import TracebackType
from typing import Any
from typing import Optional
from typing import Protocol
from typing import cast

from ddtrace._trace.subscribers.web_framework import WebFrameworkRequestSubscriber
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.contrib.internal.flask.request import prepare_request
from ddtrace.contrib.internal.web_framework import CompleteCallback
from ddtrace.contrib.internal.wsgi.lifecycle import WSGIResponseIterable
from ddtrace.internal import core
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace.internal.span_bus import span_from_context


RequestScope = core.ExecutionContext[WebFrameworkRequestEvent]
ScopeRunner = Callable[[RequestScope, Callable[[], Any]], Any]
Application = Callable[[RequestScope, dict[str, Any], Callable[..., Any]], Iterable[bytes]]
ExcInfo = tuple[Optional[type], Optional[BaseException], Optional[TracebackType]]


class DeferredRequestRunner(Protocol):
    """Proposed shared-runner extension; existing callers keep synchronous scope exit.

    The shared runner prepares metadata and creates the typed context. When given
    run_in_scope, it delegates entry/execution/exit to that callback instead of
    entering the context itself. The callback receives the existing invocation
    closure, including safe metadata completion and exception handling.

    Preparation failure must bypass BOTH scope and adapter and invoke the supplied
    callback once. The Flask callback below therefore includes an untraced fallback.
    The existing execute parameter remains unchanged; it is omitted here because
    Flask continues to use its legacy security callbacks in this migration.
    """

    def __call__(
        self,
        callback: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        *,
        prepare: Callable[[], WebFrameworkRequestEvent],
        complete: CompleteCallback,
        context_name_override: str,
        run_in_scope: ScopeRunner,
    ) -> Any: ...


def run_flask_request(
    runner: DeferredRequestRunner,
    application: Application,
    fallback: Callable[..., Iterable[bytes]],
    middleware: Any,
    environ: dict[str, Any],
    start_response: Callable[..., Any],
    integration_config: IntegrationConfig,
    flask_version: str,
) -> Iterable[bytes]:
    """Proposed replacement for Flask's inherited WSGI request-context setup.

    application is the existing middleware application executor, retaining child
    spans and security callbacks. fallback is the original uninstrumented WSGI
    callable. The caller still emits request-starting before this function.
    """
    request_scope: Optional[RequestScope] = None

    def prepare() -> WebFrameworkRequestEvent:
        event = prepare_request(environ, integration_config)
        event.tags["flask.version"] = flask_version
        return event

    def run_in_scope(scope: RequestScope, invoke: Callable[[], Any]) -> WSGIResponseIterable:
        nonlocal request_scope
        request_scope = scope
        # Transitional aliases consumed by existing child-span and security hooks.
        # req_span is supplied by the tracing subscriber, never by contrib.
        scope.set_items(
            {
                "middleware": middleware,
                "environ": environ,
                "headers": scope.event.request_headers,
                "flask_request_event": scope.event,
                "defer_response_lifecycle": True,
            }
        )
        return WSGIResponseIterable(scope).start(invoke)

    def invoke() -> Iterable[bytes]:
        if request_scope is None:
            return fallback(environ, start_response)
        try:
            return application(request_scope, environ, start_response)
        finally:
            # Preserve AppSec/IAST cleanup timing without ending the typed context.
            core.dispatch("context.ended.wsgi.__call__", (request_scope, sys.exc_info()))

    def complete(event: WebFrameworkRequestEvent, result: Any, error: Optional[BaseException]) -> None:
        # finalize_request and start_response update this event while Flask has a
        # response object. The returned WSGI iterable cannot supply status/headers;
        # do not call complete_response on it and erase the captured metadata.
        pass

    result = runner(
        invoke,
        (),
        {},
        prepare=prepare,
        complete=complete,
        context_name_override="flask.request",
        run_in_scope=run_in_scope,
    )
    return cast(Iterable[bytes], result)


class DraftFlaskRequestSubscriber(WebFrameworkRequestSubscriber):
    """Draft specialization for Flask's response span; intentionally unregistered."""

    auto_register = False
    # Activation also needs the shared subscriber's inferred fixed-length tuple
    # annotation widened to Sequence[str], as on its ContextSubscriber base.
    event_names = ("flask.request",)  # type: ignore[assignment]

    @classmethod
    def on_started(cls, ctx: RequestScope) -> None:
        span = span_from_context(ctx)
        ctx.set_item("req_span", span)
        span._ignore_exception(StopIteration)

    @classmethod
    def _on_context_ended(cls, ctx: RequestScope, exc_info: ExcInfo) -> None:
        response_span = ctx.get_item("resp_span")
        if response_span is not None:
            if exc_info[0] is not None and not issubclass(exc_info[0], StopIteration):
                response_span.set_exc_info(*exc_info)
            response_span.finish()
            # Preserve existing attribution: stream/close errors affect the response
            # span; application failures before a response affect the request span.
            exc_info = (None, None, None)
        super()._on_context_ended(ctx, exc_info)
