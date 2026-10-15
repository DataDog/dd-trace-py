from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import MutableMapping
from contextvars import copy_context
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


class _RequestLifecycle:
    """IDM owns completion; core provides the context and span lifecycle machinery."""

    def __init__(self, service: str, environ: dict[str, Any], app: Any) -> None:
        self.app = app
        req = BaseRequest(environ)
        self.event = WebFrameworkRequestEvent(
            http_operation="bottle.request",
            service=service,
            component=config.bottle.integration_name,
            request_headers=dict(req.headers),
            request_url=req.url,
            query=req.query_string,
            request_method=req.method,
            headers_case_sensitive=True,
            integration_config=config.bottle,
            activate_distributed_headers=True,
        )
        self.context = copy_context()
        self.provider = tracer.context_provider
        self.ctx = core.context_with_event(self.event)
        self.exc_info: _ExcInfo = (None, None, None)
        self.completed = False
        self.response_committed = False

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
        self.event.resource = f"{self.event.request_method} {route.rule}"
        self.event.request_route = "/".join([request.script_name.rstrip("/"), route.rule.lstrip("/")])
        span_from_context(self.ctx).resource = self.event.resource

    def record_exception(self, exc_info: _ExcInfo) -> None:
        if not self.completed and exc_info[0] is not None:
            self.exc_info = exc_info

    def finish(self) -> None:
        if self.completed:
            return
        self.completed = True
        if not self.event.resource:
            self.event.resource = f"{self.event.request_method} {self.event.response_status_code or 500}"
        try:
            self.ctx.__exit__(*self.exc_info)
        finally:
            self.exc_info = (None, None, None)

    def run(self, application: Callable[..., Any], environ: dict[str, Any], start_response: Callable[..., Any]) -> Any:
        self.ctx.__enter__()
        previous = environ.get(_REQUEST_SCOPE)
        environ[_REQUEST_SCOPE] = self

        def response_ready(status: str, headers: list[tuple[str, str]], exc_info: Any = None) -> Any:
            self.event.response_status_code = int(status.split(" ", 1)[0])
            self.event.response_headers = dict(headers)
            if exc_info:
                self.record_exception(exc_info)
            result = start_response(status, headers, exc_info)
            self.response_committed = True
            return result

        try:
            body = application(environ, response_ready)
            return _ResponseIterator(body, self)
        except BaseException:
            self.record_exception(sys.exc_info())
            if not self.response_committed:
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
            if plugin is None or not is_tracing_enabled():
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
            if not is_tracing_enabled():
                return callback(*args, **kwargs)

            lifecycle = request.environ.get(_REQUEST_SCOPE)
            if lifecycle is not None:
                try:
                    return callback(*args, **kwargs)
                except BaseException:
                    lifecycle.record_exception(sys.exc_info())
                    raise

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
