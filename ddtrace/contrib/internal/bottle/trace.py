from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import MutableMapping
from contextvars import copy_context
from dataclasses import replace
import sys
from threading import get_ident
from types import TracebackType
from typing import Any
from typing import Optional
from typing import TypeVar
from typing import cast

from bottle import BaseRequest
from bottle import HTTPError
from bottle import HTTPResponse
from bottle import request
from bottle import response

from ddtrace import config
from ddtrace.contrib._events.http import HttpBlockingResponse
from ddtrace.contrib._events.http import HttpDecisionAcceptance
from ddtrace.contrib._events.http import HttpEnforcementAction
from ddtrace.contrib._events.http import HttpInspectionDecision
from ddtrace.contrib._events.http import HttpRequestData
from ddtrace.contrib._events.http import HttpRequestDataEvent
from ddtrace.contrib._events.http import HttpRequestError
from ddtrace.contrib._events.http import HttpRequestInspectionEvent
from ddtrace.contrib._events.http import HttpRequestPhase
from ddtrace.contrib._events.http import HttpRequestSnapshot
from ddtrace.contrib._events.http import HttpResponseData
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.contrib.internal.trace_utils import is_tracing_enabled
from ddtrace.internal import core
from ddtrace.internal.span_bus import span_from_context
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.internal.utils.deprecations import deprecate
from ddtrace.internal.utils.formats import asbool
from ddtrace.trace import tracer


_REQUEST_SCOPE = "datadog.bottle.request"
_ExcInfo = tuple[Optional[type], Optional[BaseException], Optional[TracebackType]]
_Result = TypeVar("_Result")


# WSGI adapters own framing and cannot control hop-by-hop transport headers.
_TRANSPORT_HEADERS = frozenset(
    (
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",  # codespell:ignore te
        "trailer",
        "transfer-encoding",
        "upgrade",
        "content-length",
    )
)
_HEADER_TOKEN = frozenset("!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")


def _valid_blocking_response(value: Any) -> bool:
    if not isinstance(value, HttpBlockingResponse):
        return False
    value = cast(Any, value)
    if type(value.status_code) is not int or not 200 <= value.status_code <= 599:
        return False
    if not isinstance(value.body, bytes) or not isinstance(value.headers, tuple):
        return False
    if value.status_code in (204, 205, 304) and value.body:
        return False
    for pair in value.headers:
        if not isinstance(pair, tuple) or len(pair) != 2:
            return False
        name, header = pair
        if not isinstance(name, str) or not isinstance(header, str):
            return False
        if not name or not set(name) <= _HEADER_TOKEN or name.lower() in _TRANSPORT_HEADERS:
            return False
        if any(ord(char) < 32 or ord(char) == 127 or ord(char) > 255 for char in header):
            return False
    return True


def _blocking_response(value: HttpBlockingResponse) -> HTTPResponse:
    result = HTTPResponse(body=value.body, status=value.status_code, headers=value.headers)
    if value.status_code not in (204, 304):
        result.content_length = len(value.body)
    return result


class _RequestLifecycle:
    """IDM owns completion; core provides the context and span lifecycle machinery."""

    def __init__(self, service: str, environ: dict[str, Any], app: Any, tracing_enabled: bool) -> None:
        self.app = app
        req = BaseRequest(environ)
        self.request_id = object()
        self.request_data = HttpRequestData(
            method=req.method,
            url=req.url,
            headers=tuple(req.headers.items()),
            query_string=req.query_string,
            query_parameters=tuple(req.query.allitems()),
        )
        self.response_data: Optional[HttpResponseData] = None
        self.errors: tuple[HttpRequestError, ...] = ()
        self.handler_started = False
        self.response_committed = False
        self.completed = False
        self.terminate = False
        self.event: Optional[WebFrameworkRequestEvent] = None
        if tracing_enabled:
            self.event = WebFrameworkRequestEvent(
                http_operation="bottle.request",
                service=service,
                component=config.bottle.integration_name,
                request_headers=dict(self.request_data.headers or ()),
                request_url=self.request_data.url,
                query=self.request_data.query_string,
                request_method=self.request_data.method,
                headers_case_sensitive=True,
                integration_config=config.bottle,
                activate_distributed_headers=True,
            )
        self.context = copy_context()
        self.provider = tracer.context_provider
        self.ctx = (
            core.context_with_event(self.event)
            if self.event is not None
            else core.context_with_data("bottle.request.lifecycle")
        )
        self.exc_info: _ExcInfo = (None, None, None)

    def snapshot(self, phase: HttpRequestPhase) -> HttpRequestSnapshot:
        actions: frozenset[HttpEnforcementAction] = frozenset()
        if not self.completed:
            if self.response_committed:
                if phase == HttpRequestPhase.RESPONSE_COMMITTED:
                    actions = frozenset((HttpEnforcementAction.TERMINATE,))
            elif phase != HttpRequestPhase.COMPLETED:
                actions = frozenset((HttpEnforcementAction.RESPOND,))
        return HttpRequestSnapshot(
            request_id=self.request_id,
            framework="bottle",
            phase=phase,
            request=self.request_data,
            response=self.response_data,
            errors=self.errors,
            handler_started=self.handler_started,
            response_committed=self.response_committed,
            supported_actions=actions,
        )

    def publish(self, phase: HttpRequestPhase) -> None:
        if core.has_listeners(HttpRequestDataEvent.event_name):
            core.dispatch_event(HttpRequestDataEvent(snapshot=self.snapshot(phase)))

    def inspect(self, phase: HttpRequestPhase) -> Optional[HttpInspectionDecision]:
        if not core.has_listeners(HttpRequestInspectionEvent.event_name):
            return None
        snapshot = self.snapshot(phase)
        decision: Optional[HttpInspectionDecision] = None
        opened = True
        owner = get_ident()

        def submit(candidate: Any) -> HttpDecisionAcceptance:
            nonlocal decision
            if not opened or get_ident() != owner:
                return HttpDecisionAcceptance.CLOSED
            if not isinstance(candidate, HttpInspectionDecision):
                return HttpDecisionAcceptance.INVALID
            candidate = cast(Any, candidate)
            if candidate.action is None:
                return HttpDecisionAcceptance.ACCEPTED if candidate.response is None else HttpDecisionAcceptance.INVALID
            if not isinstance(candidate.action, HttpEnforcementAction):
                return HttpDecisionAcceptance.INVALID
            if candidate.action not in snapshot.supported_actions:
                return HttpDecisionAcceptance.UNSUPPORTED
            if candidate.action == HttpEnforcementAction.RESPOND:
                if not _valid_blocking_response(candidate.response):
                    return HttpDecisionAcceptance.INVALID
            elif candidate.response is not None:
                return HttpDecisionAcceptance.INVALID
            if decision is not None:
                return HttpDecisionAcceptance.ALREADY_DECIDED
            decision = candidate
            return HttpDecisionAcceptance.ACCEPTED

        try:
            core.dispatch_event(HttpRequestInspectionEvent(snapshot=snapshot, submit=submit))
        finally:
            opened = False
        return decision

    def call(self, function: Callable[..., _Result], *args: Any) -> _Result:
        previous = self.provider.active()
        try:
            return self.context.run(self._call, function, args)
        finally:
            # Context.run restores contextvars, but activation subscribers (such as
            # profiling) also need notification when the request yields to its caller.
            self.provider.activate(previous)

    def _call(self, function: Callable[..., _Result], args: tuple[Any, ...]) -> _Result:
        self.provider.activate(self.provider.active())
        return function(*args)

    def resolve_route(self, route: Any) -> None:
        full_route = "/".join([request.script_name.rstrip("/"), route.rule.lstrip("/")])
        self.request_data = replace(self.request_data, route=full_route)
        if self.event is not None:
            self.event.resource = f"{self.request_data.method} {route.rule}"
            self.event.request_route = full_route
            span_from_context(self.ctx).resource = self.event.resource
        self.publish(HttpRequestPhase.ROUTE_RESOLVED)
        decision = self.inspect(HttpRequestPhase.ROUTE_RESOLVED)
        if decision is not None and decision.response is not None:
            raise _blocking_response(decision.response)

    def before_handler(self) -> Optional[HTTPResponse]:
        self.publish(HttpRequestPhase.BEFORE_HANDLER)
        decision = self.inspect(HttpRequestPhase.BEFORE_HANDLER)
        if decision is not None and decision.response is not None:
            return _blocking_response(decision.response)
        self.handler_started = True
        return None

    def record_exception(self, exc_info: _ExcInfo) -> None:
        if not self.completed and exc_info[0] is not None:
            self.exc_info = exc_info
            error_type = exc_info[0]
            try:
                message = str(exc_info[1])
            except Exception:
                message = None
            self.errors = (HttpRequestError(f"{error_type.__module__}.{error_type.__qualname__}", message),)

    def finish(self) -> None:
        if self.completed:
            return
        self.completed = True
        if self.event is not None and not self.event.resource:
            self.event.resource = f"{self.request_data.method} {self.event.response_status_code or 500}"
        try:
            self.publish(HttpRequestPhase.COMPLETED)
        finally:
            try:
                self.ctx.__exit__(*self.exc_info)
            finally:
                self.exc_info = (None, None, None)

    def run(self, application: Callable[..., Any], environ: dict[str, Any], start_response: Callable[..., Any]) -> Any:
        self.ctx.__enter__()
        previous = environ.get(_REQUEST_SCOPE)
        environ[_REQUEST_SCOPE] = self

        replacement: Optional[HTTPResponse] = None

        def response_ready(status: str, headers: list[tuple[str, str]], exc_info: Any = None) -> Any:
            nonlocal replacement
            replacement = None
            self.response_data = HttpResponseData(status_code=int(status.split(" ", 1)[0]), headers=tuple(headers))
            if self.event is not None:
                self.event.response_status_code = self.response_data.status_code
                self.event.response_headers = dict(headers)
            if exc_info:
                self.record_exception(exc_info)
            self.publish(HttpRequestPhase.RESPONSE_READY)
            decision = self.inspect(HttpRequestPhase.RESPONSE_READY)
            if decision is not None and decision.response is not None:
                replacement = _blocking_response(decision.response)
                status, headers = replacement.status, replacement.headerlist
                self.response_data = HttpResponseData(status_code=replacement.status_code, headers=tuple(headers))
                if self.event is not None:
                    self.event.response_status_code = replacement.status_code
                    self.event.response_headers = dict(headers)
            result = start_response(status, headers, exc_info)
            self.response_committed = True
            self.publish(HttpRequestPhase.RESPONSE_COMMITTED)
            decision = self.inspect(HttpRequestPhase.RESPONSE_COMMITTED)
            self.terminate = decision is not None and decision.action == HttpEnforcementAction.TERMINATE
            return result

        try:
            self.publish(HttpRequestPhase.REQUEST_STARTED)
            decision = self.inspect(HttpRequestPhase.REQUEST_STARTED)
            if decision is not None and decision.response is not None:
                blocked = _blocking_response(decision.response)
                body = [blocked.body]
                response_ready(blocked.status, blocked.headerlist)
            else:
                body = application(environ, response_ready)
            if replacement is not None or self.terminate:
                close = getattr(body, "close", None)
                if close is not None:
                    close()
                body = [replacement.body] if replacement is not None and not self.terminate else []
            if self.request_data.method == "HEAD":
                close = getattr(body, "close", None)
                if close is not None:
                    close()
                body = []
            return _ResponseIterator(body, self)
        except BaseException:
            self.record_exception(sys.exc_info())
            if not self.response_committed and self.event is not None:
                self.event.response_status_code = 500
            self.finish()
            raise
        finally:
            if previous is None:
                environ.pop(_REQUEST_SCOPE, None)
            else:
                environ[_REQUEST_SCOPE] = previous


class _ResponseIterator:
    """Keep the request active only while producing or closing its WSGI body."""

    def __init__(self, body: Iterable[bytes], lifecycle: _RequestLifecycle) -> None:
        self.body = body
        self.iterator = iter(body)
        self.lifecycle = lifecycle
        self.closed = False

    def __iter__(self) -> "_ResponseIterator":
        return self

    def __next__(self) -> bytes:
        return self.lifecycle.call(self._next)

    def _next(self) -> bytes:
        if self.lifecycle.completed:
            raise StopIteration
        try:
            return next(self.iterator)
        except StopIteration:
            self.lifecycle.finish()
            raise
        except BaseException:
            self.lifecycle.record_exception(sys.exc_info())
            self.lifecycle.finish()
            raise

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.lifecycle.call(self._close)

    def _close(self) -> None:
        try:
            close = getattr(self.body, "close", None)
            if close is not None:
                close()
        except BaseException:
            self.lifecycle.record_exception(sys.exc_info())
            raise
        finally:
            self.lifecycle.finish()


class TracePlugin:
    name = "trace"
    api = 2

    def __init__(self, service="bottle", tracer=None, distributed_tracing=None):
        if tracer is not None:
            deprecate(
                "The tracer parameter is deprecated",
                message="The global tracer will be used instead.",
                category=DDTraceDeprecationWarning,
                removal_version="5.0.0",
            )
        self.service = config._get_service(default=service)
        if distributed_tracing is not None:
            config.bottle.distributed_tracing = distributed_tracing

    def setup(self, app: Any) -> None:
        if app.__dict__.get("_datadog_lifecycle_installed"):
            return
        app.__dict__["_datadog_lifecycle_installed"] = True
        application = app.wsgi
        cast_response = app._cast
        match_route = app.router.match

        def traced_wsgi(environ: dict[str, Any], start_response: Callable[..., Any]) -> Any:
            # Resolve the installed plugin on each request so uninstall/reinstall works.
            plugin = next((p for p in reversed(app.plugins) if isinstance(p, TracePlugin)), None)
            tracing_enabled = is_tracing_enabled()
            if plugin is None or not (
                tracing_enabled
                or core.has_listeners(HttpRequestDataEvent.event_name)
                or core.has_listeners(HttpRequestInspectionEvent.event_name)
            ):
                return application(environ, start_response)
            lifecycle = _RequestLifecycle(plugin.service, environ, app, tracing_enabled)
            return lifecycle.call(lifecycle.run, application, environ, start_response)

        def traced_match(environ: dict[str, Any]) -> Any:
            route, arguments = match_route(environ)
            lifecycle = environ.get(_REQUEST_SCOPE)
            if lifecycle is not None and lifecycle.app is app:
                lifecycle.resolve_route(route)
            return route, arguments

        def traced_cast(out: Any, *args: Any, **kwargs: Any) -> Any:
            lifecycle = request.environ.get(_REQUEST_SCOPE)
            # Bottle converts hook and first-chunk failures into HTTPError before
            # returning to WSGI, so capture the original exception here.
            if (
                lifecycle is not None
                and lifecycle.app is app
                and isinstance(out, HTTPError)
                and out.exception is not None
            ):
                error = out.exception
                lifecycle.record_exception((type(error), error, error.__traceback__))
            return cast_response(out, *args, **kwargs)

        app.__dict__["wsgi"] = traced_wsgi
        app.__dict__["_cast"] = traced_cast
        app.router.match = traced_match

    @property
    def distributed_tracing(self):
        return config.bottle.distributed_tracing

    @distributed_tracing.setter
    def distributed_tracing(self, distributed_tracing):
        config.bottle["distributed_tracing"] = asbool(distributed_tracing)

    def apply(self, callback: Callable[..., Any], route: Any) -> Callable[..., Any]:
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            lifecycle = request.environ.get(_REQUEST_SCOPE)
            if lifecycle is not None:
                try:
                    blocked = lifecycle.before_handler()
                    if blocked is not None:
                        return blocked
                    return callback(*args, **kwargs)
                except BaseException:
                    lifecycle.record_exception(sys.exc_info())
                    raise

            if not is_tracing_enabled():
                return callback(*args, **kwargs)

            # Route-local plugins do not receive setup(), so retain their callback scope.
            event = WebFrameworkRequestEvent(
                http_operation="bottle.request",
                service=self.service,
                resource=f"{request.method} {route.rule}",
                component=config.bottle.integration_name,
                request_headers=cast(MutableMapping[str, str], request.headers),
                request_url=request.url,
                query=request.query_string,
                request_route="/".join([request.script_name.rstrip("/"), route.rule.lstrip("/")]),
                request_method=request.method,
                integration_config=config.bottle,
                headers_case_sensitive=True,
                activate_distributed_headers=True,
            )
            with core.context_with_event(event):
                code = None
                result = None
                try:
                    result = callback(*args, **kwargs)
                    return result
                except (HTTPError, HTTPResponse) as e:
                    # you can interrupt flows using abort(status_code, 'message')...
                    # we need to respect the defined status_code.
                    # we also need to handle when response is raised as is the
                    # case with a 4xx status
                    code = e.status_code
                    raise
                except Exception:
                    # bottle doesn't always translate unhandled exceptions, so
                    # we mark it here.
                    code = 500
                    raise
                finally:
                    # Bottle's local response carries headers even when a returned
                    # or raised HTTPResponse overrides the status.
                    event.set_response(response)
                    if isinstance(result, HTTPResponse):
                        event.response_status_code = result.status_code
                    elif code:
                        event.response_status_code = code

        return wrapped
