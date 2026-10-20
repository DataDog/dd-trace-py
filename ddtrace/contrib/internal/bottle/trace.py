from collections.abc import Callable
from functools import wraps
import sys
from typing import Any
from typing import Optional

from bottle import BaseRequest
from bottle import HTTPError
from bottle import HTTPResponse
from bottle import request
from bottle import response

from ddtrace import config
from ddtrace.contrib._events.http import HttpBlockingResponse
from ddtrace.contrib._events.http import HttpRequestData
from ddtrace.contrib._events.http import HttpRequestError
from ddtrace.contrib._events.http import HttpRequestPhase
from ddtrace.contrib._events.http import HttpResponseData
from ddtrace.contrib.internal.http_server import HttpLifecycle
from ddtrace.contrib.internal.http_server import HttpLifecycleConfig
from ddtrace.contrib.internal.http_server import create_http_server_context
from ddtrace.contrib.internal.http_server import publish_http_event
from ddtrace.contrib.internal.http_server import request_error
from ddtrace.contrib.internal.http_wsgi import WsgiLifecycleDriver
from ddtrace.contrib.internal.http_wsgi import current_http_lifecycle
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.internal.utils.deprecations import deprecate
from ddtrace.internal.utils.formats import asbool


def _blocking_response(value: HttpBlockingResponse) -> HTTPResponse:
    result = HTTPResponse(body=value.body, status=value.status_code, headers=value.headers)
    if value.status_code not in (204, 304):
        result.content_length = len(value.body)
    return result


def _instrument_handler(route: Any, current: Callable[[dict[str, Any]], Optional[HttpLifecycle]]) -> None:
    if route.__dict__.get("_datadog_handler_installed"):
        return
    make_callback = route._make_callback

    def traced_make_callback() -> Callable[..., Any]:
        callback = make_callback()

        @wraps(callback)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            lifecycle = current(request.environ)
            if lifecycle is None:
                return callback(*args, **kwargs)
            try:
                blocked = lifecycle.before_handler()
                if blocked is not None:
                    return _blocking_response(blocked)
                return callback(*args, **kwargs)
            except BaseException:
                lifecycle.record_exception(sys.exc_info())
                raise

        return wrapped

    # Wrap outside the plugin chain so skip settings cannot disable HTTP checks.
    # Wrapping the factory also preserves checks when Bottle resets its call cache.
    route._make_callback = traced_make_callback
    route.__dict__["_datadog_handler_installed"] = True
    route.reset()


def _trace_route_callback(
    callback: Callable[..., Any], route: Any, service: str, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
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
        publish_http_event(
            phase,
            request_id=request_id,
            framework="bottle",
            request=request_data,
            **fields,
        )

    with create_http_server_context(
        framework="bottle",
        service=service,
        request_id=request_id,
        request_headers=dict(request_data.headers or ()),
        integration_config=config.bottle,
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
            errors = (request_error(type(e), e),)
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


def _request_data(environ: dict[str, Any]) -> HttpRequestData:
    req = BaseRequest(environ)
    return HttpRequestData(
        method=req.method,
        url=req.url,
        headers=tuple(req.headers.items()),
        query_string=req.query_string,
        query_parameters=tuple(req.query.allitems()),
    )


def _install_hooks(app: Any, current: Callable[[dict[str, Any]], Optional[HttpLifecycle]]) -> None:
    cast_response = app._cast
    match_route = app.router.match

    def traced_match(environ: dict[str, Any]) -> Any:
        route, arguments = match_route(environ)
        lifecycle = current(environ)
        if lifecycle is not None:
            full_route = "/".join([request.script_name.rstrip("/"), route.rule.lstrip("/")])
            block = lifecycle.route_resolved(full_route, route.rule)
            if block is not None:
                raise _blocking_response(block)
            _instrument_handler(route, current)
        return route, arguments

    def traced_cast(out: Any, *args: Any, **kwargs: Any) -> Any:
        lifecycle = current(request.environ)
        # Bottle converts hook and first-chunk failures into HTTPError before
        # returning to WSGI, so capture the original exception here.
        if lifecycle is not None and isinstance(out, HTTPError) and out.exception is not None:
            error = out.exception
            lifecycle.record_exception((type(error), error, error.__traceback__))
        return cast_response(out, *args, **kwargs)

    app.__dict__["_cast"] = traced_cast
    app.router.match = traced_match


def _install_lifecycle(app: Any) -> None:
    if app.__dict__.get("_datadog_lifecycle_installed"):
        return
    app.__dict__["_datadog_lifecycle_installed"] = True

    def get_config() -> Optional[HttpLifecycleConfig]:
        # Resolve the installed plugin on each request so uninstall/reinstall works.
        plugin = next((p for p in reversed(app.plugins) if isinstance(p, TracePlugin)), None)
        if plugin is None:
            return None
        return HttpLifecycleConfig("bottle", plugin.service, config.bottle)

    driver = WsgiLifecycleDriver(
        get_config=get_config, request_data=_request_data, blocking_response=_blocking_response
    )
    _install_hooks(app, driver.current)
    app.__dict__["wsgi"] = driver.wrap(app.wsgi)


def _wrap_callback(callback: Callable[..., Any], route: Any, service: Callable[[], str]) -> Callable[..., Any]:
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        lifecycle = current_http_lifecycle(request.environ)
        if lifecycle is not None:
            try:
                return callback(*args, **kwargs)
            except BaseException:
                # Outer Bottle plugins may turn raised HTTP responses into values.
                # Preserve error capture before that conversion as well as at entry.
                lifecycle.record_exception(sys.exc_info())
                raise

        return _trace_route_callback(callback, route, service(), args, kwargs)

    return wrapped


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
        _install_lifecycle(app)

    @property
    def distributed_tracing(self):
        return config.bottle.distributed_tracing

    @distributed_tracing.setter
    def distributed_tracing(self, distributed_tracing):
        config.bottle["distributed_tracing"] = asbool(distributed_tracing)

    def apply(self, callback: Callable[..., Any], route: Any) -> Callable[..., Any]:
        return _wrap_callback(callback, route, lambda: self.service)
