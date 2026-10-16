from collections.abc import Callable
from collections.abc import Iterable
from contextvars import copy_context
from dataclasses import replace
import sys
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
from ddtrace.contrib._events.bottle import BottleRequestEvent
from ddtrace.contrib._events.http import HTTP_REQUEST_DATA_EVENTS
from ddtrace.contrib._events.http import HttpBlockingResponse
from ddtrace.contrib._events.http import HttpRequestBlock
from ddtrace.contrib._events.http import HttpRequestBlockCheckEvent
from ddtrace.contrib._events.http import HttpRequestData
from ddtrace.contrib._events.http import HttpRequestError
from ddtrace.contrib._events.http import HttpRequestPhase
from ddtrace.contrib._events.http import HttpResponseData
from ddtrace.internal import core
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.internal.utils.deprecations import deprecate
from ddtrace.internal.utils.formats import asbool
from ddtrace.trace import tracer


_REQUEST_SCOPE = "datadog.bottle.request"
# Every name a consumer can subscribe to for a Bottle request. Nothing listening means
# nothing to produce; whether tracing is enabled is a subscriber's concern, not ours.
_LIFECYCLE_EVENTS = (
    f"context.started.{BottleRequestEvent.event_name}",
    HttpRequestBlockCheckEvent.event_name,
    *(event.event_name for event in HTTP_REQUEST_DATA_EVENTS.values()),
)
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


def _request_error(error_type: type, error: BaseException) -> HttpRequestError:
    try:
        message: Optional[str] = str(error)
    except Exception:
        message = None
    return HttpRequestError(f"{error_type.__module__}.{error_type.__qualname__}", message)


def _publish(phase: HttpRequestPhase, **fields: Any) -> None:
    event = HTTP_REQUEST_DATA_EVENTS[phase]
    if core.has_listeners(event.event_name):
        core.dispatch_event(event(phase=phase, **fields))


def _blocking_response(value: HttpBlockingResponse) -> HTTPResponse:
    result = HTTPResponse(body=value.body, status=value.status_code, headers=value.headers)
    if value.status_code not in (204, 304):
        result.content_length = len(value.body)
    return result


class _RequestLifecycle:
    """IDM owns completion; core provides the context and span lifecycle machinery."""

    def __init__(self, service: str, environ: dict[str, Any], app: Any) -> None:
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
        self.blocked = False
        self.context = copy_context()
        self.provider = tracer.context_provider
        self.ctx = core.context_with_event(
            BottleRequestEvent(
                service=service,
                component=config.bottle.integration_name,
                request_headers=dict(self.request_data.headers or ()),
                integration_config=config.bottle,
                activate_distributed_headers=True,
            )
        )
        self.exc_info: _ExcInfo = (None, None, None)

    def event_fields(self) -> dict[str, Any]:
        return dict(
            request_id=self.request_id,
            framework="bottle",
            request=self.request_data,
            response=self.response_data,
            errors=self.errors,
            handler_started=self.handler_started,
            response_committed=self.response_committed,
        )

    def publish(self, phase: HttpRequestPhase) -> None:
        if core.has_listeners(HTTP_REQUEST_DATA_EVENTS[phase].event_name):
            _publish(phase, **self.event_fields())

    def check_block(self, phase: HttpRequestPhase) -> Optional[HTTPResponse]:
        if self.blocked or self.response_committed or not core.has_listeners(HttpRequestBlockCheckEvent.event_name):
            return None
        try:
            core.dispatch_event(HttpRequestBlockCheckEvent(phase=phase, **self.event_fields()))
        except HttpRequestBlock as block:
            if _valid_blocking_response(block.response):
                self.blocked = True
                return _blocking_response(block.response)
        return None

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
        self.request_data = replace(self.request_data, route=full_route, route_pattern=route.rule)
        self.publish(HttpRequestPhase.ROUTE_RESOLVED)
        blocked = self.check_block(HttpRequestPhase.ROUTE_RESOLVED)
        if blocked is not None:
            raise blocked

    def before_handler(self) -> Optional[HTTPResponse]:
        self.publish(HttpRequestPhase.BEFORE_HANDLER)
        blocked = self.check_block(HttpRequestPhase.BEFORE_HANDLER)
        if blocked is not None:
            return blocked
        self.handler_started = True
        return None

    def record_exception(self, exc_info: _ExcInfo) -> None:
        if not self.completed and exc_info[0] is not None:
            self.exc_info = exc_info
            self.errors = (_request_error(exc_info[0], cast(BaseException, exc_info[1])),)

    def finish(self) -> None:
        if self.completed:
            return
        self.completed = True
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
            if exc_info:
                self.record_exception(exc_info)
            self.publish(HttpRequestPhase.RESPONSE_READY)
            replacement = self.check_block(HttpRequestPhase.RESPONSE_READY)
            if replacement is not None:
                status, headers = replacement.status, replacement.headerlist
                self.response_data = HttpResponseData(status_code=replacement.status_code, headers=tuple(headers))
            result = start_response(status, headers, exc_info)
            self.response_committed = True
            self.publish(HttpRequestPhase.RESPONSE_COMMITTED)
            return result

        try:
            self.publish(HttpRequestPhase.REQUEST_STARTED)
            blocked = self.check_block(HttpRequestPhase.REQUEST_STARTED)
            if blocked is not None:
                body = [blocked.body]
                response_ready(blocked.status, blocked.headerlist)
            else:
                body = application(environ, response_ready)
            if replacement is not None:
                close = getattr(body, "close", None)
                if close is not None:
                    close()
                body = [replacement.body]
            if self.request_data.method == "HEAD":
                close = getattr(body, "close", None)
                if close is not None:
                    close()
                body = []
            return _ResponseIterator(body, self)
        except BaseException:
            self.record_exception(sys.exc_info())
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
            if plugin is None or not any(core.has_listeners(name) for name in _LIFECYCLE_EVENTS):
                return application(environ, start_response)
            lifecycle = _RequestLifecycle(plugin.service, environ, app)
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

            # Route-local plugins do not receive setup(), so this path sees only the
            # callback: no WSGI boundary, so no route resolution or response phases.
            request_id = object()
            request_data = HttpRequestData(
                method=request.method,
                url=request.url,
                headers=tuple(request.headers.items()),
                query_string=request.query_string,
                query_parameters=tuple(request.query.allitems()),
                route="/".join([request.script_name.rstrip("/"), route.rule.lstrip("/")]),
                route_pattern=route.rule,
            )

            def publish(phase: HttpRequestPhase, **fields: Any) -> None:
                _publish(
                    phase,
                    request_id=request_id,
                    framework="bottle",
                    request=request_data,
                    **fields,
                )

            with core.context_with_event(
                BottleRequestEvent(
                    service=self.service,
                    component=config.bottle.integration_name,
                    request_headers=dict(request_data.headers or ()),
                    integration_config=config.bottle,
                    activate_distributed_headers=True,
                )
            ):
                # The route is already matched when a route-local plugin runs, so this
                # phase is announced before the handler rather than discovered at the
                # WSGI boundary.
                publish(HttpRequestPhase.ROUTE_RESOLVED)
                code = None
                result = None
                errors: tuple[HttpRequestError, ...] = ()
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
                except Exception as e:
                    # bottle doesn't always translate unhandled exceptions, so
                    # we mark it here.
                    code = 500
                    errors = (_request_error(type(e), e),)
                    raise
                finally:
                    # Bottle's local response carries headers even when a returned
                    # or raised HTTPResponse overrides the status.
                    status_code = response.status_code
                    if isinstance(result, HTTPResponse):
                        status_code = result.status_code
                    elif code:
                        status_code = code
                    publish(
                        HttpRequestPhase.COMPLETED,
                        response=HttpResponseData(status_code=status_code, headers=tuple(response.headerlist)),
                        errors=errors,
                        handler_started=True,
                    )

        return wrapped
