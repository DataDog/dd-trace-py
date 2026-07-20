import inspect
from typing import Any  # noqa:F401
from typing import Mapping
from typing import Optional  # noqa:F401

import starlette
from starlette import requests as starlette_requests
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib import trace_utils
from ddtrace.contrib.internal.asgi.middleware import _DD_ROUTE_RESOURCE_RESOLVER
from ddtrace.contrib.internal.asgi.middleware import TraceMiddleware
from ddtrace.contrib.internal.trace_utils import with_traced_module
from ddtrace.ext import http
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.compat import is_wrapted
from ddtrace.internal.endpoints import endpoint_collection
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.telemetry import get_config as _get_config
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils import get_blocked
from ddtrace.internal.utils import set_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.trace import Span  # noqa:F401
from ddtrace.trace import tracer
from ddtrace.vendor.packaging.version import parse as parse_version


log = get_logger(__name__)

config._add(
    "starlette",
    dict(
        _default_service="starlette",
        request_span_name="starlette.request",
        distributed_tracing=True,
        obfuscate_404_resource=env.get("DD_ASGI_OBFUSCATE_404_RESOURCE", default=False),
        trace_asgi_websocket_messages=asbool(
            _get_config("DD_TRACE_WEBSOCKET_MESSAGES_ENABLED", default=_get_config("DD_ASGI_TRACE_WEBSOCKET", True))
        ),
        asgi_websocket_messages_inherit_sampling=asbool(
            _get_config("DD_TRACE_WEBSOCKET_MESSAGES_INHERIT_SAMPLING", default=True)
        )
        and asbool(_get_config("DD_TRACE_WEBSOCKET_MESSAGES_SEPARATE_TRACES", default=True)),
        websocket_messages_separate_traces=asbool(
            _get_config("DD_TRACE_WEBSOCKET_MESSAGES_SEPARATE_TRACES", default=True)
        ),
    ),
)


def get_version() -> str:
    return getattr(starlette, "__version__", "")


_STARLETTE_VERSION = parse_version(get_version())
_STARLETTE_VERSION_LTE_0_33_0 = _STARLETTE_VERSION <= parse_version("0.33.0")


def _supported_versions() -> dict[str, str]:
    return {"starlette": ">=0.14.0"}


def traced_init(wrapped, instance, args, kwargs):
    mw = list(kwargs.pop("middleware", None) or [])
    mw.insert(
        0,
        Middleware(
            TraceMiddleware,
            integration_config=config.starlette,
            span_modifier=_set_route_resource_resolver,
        ),
    )
    kwargs.update({"middleware": mw})

    wrapped(*args, **kwargs)


def traced_route_init(wrapped, _instance, args, kwargs):
    # Endpoint registration for the endpoint_collection is NOT done here because at
    # Route.__init__ time, we don't know the mount prefix for sub-app routes.
    # Instead, _collect_routes_from_app walks the full route tree on first request
    # and registers endpoints with their complete paths (mount prefix + local route).
    handler = get_argument_value(args, kwargs, 1, "endpoint")
    core.dispatch("service_entrypoint.patch", (inspect.unwrap(handler),))
    return wrapped(*args, **kwargs)


def _collect_routes_from_app(app, prefix=""):
    """Walk an ASGI app's route tree and register all endpoints with their full paths.

    Called once on first request via the ASGI TraceMiddleware. At that point the app
    is fully constructed (all mounts done). Endpoint registration cannot happen at
    Route.__init__ time because the mount prefix is unknown then (sub-apps are
    created before being mounted).
    """
    routes = getattr(app, "routes", None)
    if not routes:
        return
    for route in routes:
        try:
            if isinstance(route, starlette.routing.Mount):
                mount_path = prefix + route.path
                _collect_routes_from_app(route, prefix=mount_path)
            elif hasattr(starlette.routing, "Host") and isinstance(route, starlette.routing.Host):
                # Host-based routing: recurse into the host's app without adding a path prefix
                _collect_routes_from_app(route, prefix=prefix)
            elif isinstance(route, starlette.routing.Route):
                full_path = prefix + route.path
                response_class = getattr(route, "response_class", None)
                media_type = getattr(response_class, "media_type", None)
                response_body_type = [media_type] if isinstance(media_type, str) else []
                response_code = getattr(route, "status_code", None)
                response_code = [response_code] if isinstance(response_code, int) else []
                for m in getattr(route, "methods", None) or []:
                    endpoint_collection.add_endpoint(
                        m,
                        full_path,
                        operation_name="fastapi.request",
                        response_body_type=response_body_type,
                        response_code=response_code,
                    )
        except Exception:
            log.debug("failed to collect endpoint for route %r", route, exc_info=True)


def _set_route_resource_resolver(_span: Span, scope: Mapping[str, Any]) -> None:
    datadog_context = scope.get("datadog")
    if datadog_context is not None:
        datadog_context[_DD_ROUTE_RESOURCE_RESOLVER] = _resolve_route_resource


def _resolve_route_resource(scope: Mapping[str, Any], span: Span) -> None:
    """Resolve a Starlette route template before ASGI finishes the request span."""
    app = scope.get("app")
    route_match = _find_matching_route_path(getattr(app, "routes", None), scope)
    if route_match is None:
        return

    path, is_route = route_match
    method = scope.get("method")
    span.resource = "{} {}".format(method, path) if method else path
    if is_route:
        span._set_attribute(http.ROUTE, path)


def _find_matching_route_path(routes: Any, scope: Mapping[str, Any], prefix: str = "") -> Optional[tuple[str, bool]]:
    if not routes:
        return None

    partial_match = None
    for route in routes:
        route_match = _match_route_path(route, scope, prefix)
        if route_match is None:
            continue

        match, resolved_route = route_match
        if match == starlette.routing.Match.FULL:
            return resolved_route
        if match == starlette.routing.Match.PARTIAL and partial_match is None:
            partial_match = resolved_route

    return partial_match


def _match_route_path(route: Any, scope: Mapping[str, Any], prefix: str) -> Optional[tuple[Any, tuple[str, bool]]]:
    host_class = getattr(starlette.routing, "Host", None)
    is_host_route = host_class is not None and isinstance(route, host_class)
    if not isinstance(route, (starlette.routing.Route, starlette.routing.Mount)) and not is_host_route:
        return None

    match, child_scope = route.matches(scope)
    if match == starlette.routing.Match.NONE:
        return None

    if isinstance(route, starlette.routing.Route):
        return match, (prefix + route.path, True)

    child_routes = getattr(route.app, "routes", None)
    if child_routes:
        updated_scope = dict(scope)
        updated_scope.update(child_scope)
        path_prefix = prefix if is_host_route else prefix + route.path
        child_match = _find_matching_route_path(child_routes, updated_scope, path_prefix)
        if child_match is not None:
            return match, child_match

    if isinstance(route, starlette.routing.Mount):
        return match, (prefix + route.path, False)
    return None


def patch():
    if getattr(starlette, "_datadog_patch", False):
        return

    starlette._datadog_patch = True

    _w("starlette.applications", "Starlette.__init__", traced_init)
    Pin().onto(starlette)

    # We need to check that Fastapi instrumentation hasn't already patched these
    if not is_wrapted(starlette.routing.Route.__init__):
        _w("starlette.routing", "Route.__init__", traced_route_init)
    if not is_wrapted(starlette.routing.Route.handle):
        _w("starlette.routing", "Route.handle", traced_handler)
    if not is_wrapted(starlette.routing.Mount.handle):
        _w("starlette.routing", "Mount.handle", traced_handler)

    if not is_wrapted(starlette.background.BackgroundTasks.add_task):
        _w("starlette.background", "BackgroundTasks.add_task", _trace_background_tasks(starlette))


def unpatch():
    if not getattr(starlette, "_datadog_patch", False):
        return

    starlette._datadog_patch = False

    _u(starlette.applications.Starlette, "__init__")

    # We need to check that Fastapi instrumentation hasn't already unpatched these
    if is_wrapted(starlette.routing.Route.handle):
        _u(starlette.routing.Route, "handle")

    if is_wrapted(starlette.routing.Mount.handle):
        _u(starlette.routing.Mount, "handle")

    if is_wrapted(starlette.background.BackgroundTasks.add_task):
        _u(starlette.background.BackgroundTasks, "add_task")


def _is_duplicate_route_call(scope: dict, instance: Any) -> bool:
    seen_routes = scope["datadog"].setdefault("_dd_seen_routes", set())
    # Route objects are app-lifetime singletons so id() is stable for the duration of the request.
    if id(instance) in seen_routes:
        return True
    seen_routes.add(id(instance))
    return False


def _get_fastapi_effective_path(scope: dict) -> Optional[str]:
    effective_route_context = scope.get("fastapi", {}).get("effective_route_context")
    if effective_route_context is None:
        return None
    return getattr(effective_route_context, "path_format", None)


def traced_handler(wrapped, instance, args, kwargs):
    # Since handle can be called multiple times for one request, we take the path of each instance
    # Then combine them at the end to get the correct resource names
    scope: Optional[dict[str, Any]] = get_argument_value(args, kwargs, 0, "scope")
    if not scope:
        return wrapped(*args, **kwargs)

    # Our ASGI TraceMiddleware has not been called, skip since
    # we won't have a request span to attach this information onto
    # DEV: This can happen if patching happens after the app has been created
    if "datadog" not in scope:
        log.warning("datadog context not present in ASGI request scope, trace middleware may be missing")
        return wrapped(*args, **kwargs)

    # FastAPI >= 0.137 calls super().handle() from APIRoute.handle, causing traced_handler to fire
    # twice for the same route instance on a single request, which doubles resource_paths.
    if _is_duplicate_route_call(scope, instance):
        return wrapped(*args, **kwargs)

    request_spans: list[Span] = scope["datadog"].get("request_spans", [])

    full_path = _get_fastapi_effective_path(scope)

    # Only accumulate resource_paths for the pre-0.137 path; when full_path is available,
    # the composed path is read directly from effective_route_context and resource_paths
    # is never consumed.
    if full_path is None:
        if "resource_paths" not in scope["datadog"]:
            scope["datadog"]["resource_paths"] = [instance.path]
        else:
            scope["datadog"]["resource_paths"].append(instance.path)

    resource_paths: list[str] = scope["datadog"].get("resource_paths", [])

    if full_path is not None:
        if request_spans:
            request_spans[0].resource = "{} {}".format(scope["method"], full_path)
            request_spans[0]._set_attribute(http.ROUTE, full_path)
    elif len(request_spans) == len(resource_paths):
        # Iterate through the request_spans and assign the correct resource name to each
        for index, span in enumerate(request_spans):
            # We want to set the full resource name on the first request span
            # And one part less of the full resource name for each proceeding request span
            # e.g. full path is /subapp/hello/{name}, first request span gets that as resource name
            # Second request span gets /hello/{name}
            path = "".join(resource_paths[index:])

            if scope.get("method"):
                span.resource = "{} {}".format(scope["method"], path)
            else:
                span.resource = path
            # route should only be in the root span
            if index == 0:
                span._set_attribute(http.ROUTE, path)
    # at least always update the root asgi span resource name request_spans[0].resource = "".join(resource_paths)
    elif request_spans and resource_paths:
        route = "".join(resource_paths)
        if scope.get("method"):
            request_spans[0].resource = "{} {}".format(scope["method"], route)
        else:
            request_spans[0].resource = route
        request_spans[0]._set_attribute(http.ROUTE, route)
    else:
        log.debug(
            "unable to update the request span resource name, request_spans:%r, resource_paths:%r",
            request_spans,
            resource_paths,
        )
    # Only run ASM/WAF processing on the final Route handler, not on intermediate Mount handlers.
    # With sub-applications, traced_handler is called for each routing layer (Mount then Route).
    # Running ASM on Mount would cause duplicate WAF triggers and incomplete path_params.
    if isinstance(instance, starlette.routing.Route):
        request_cookies = ""
        for name, value in scope.get("headers", []):
            if name == b"cookie":
                request_cookies = value.decode("utf-8", errors="ignore")
                break

        if request_spans:
            if asm_config._iast_enabled:
                from ddtrace.appsec._iast._handlers import _iast_instrument_starlette_scope

                _iast_instrument_starlette_scope(scope, request_spans[0].get_tag(http.ROUTE))

            trace_utils.set_http_meta(
                request_spans[0],
                "starlette",
                request_path_params=scope.get("path_params"),
                request_cookies=starlette_requests.cookie_parser(request_cookies),
                route=request_spans[0].get_tag(http.ROUTE),
            )
        core.dispatch("asgi.start_request", ("starlette",))
        blocked = get_blocked()
        if blocked:
            raise BlockingException(blocked)

    # https://github.com/encode/starlette/issues/1336
    if _STARLETTE_VERSION_LTE_0_33_0 and len(request_spans) > 1:
        request_spans[-1].set_tag(http.URL, request_spans[0].get_tag(http.URL))

    return wrapped(*args, **kwargs)


@with_traced_module
def _trace_background_tasks(module, pin, wrapped, instance, args, kwargs):
    task = get_argument_value(args, kwargs, 0, "func")
    current_span = tracer.current_span()
    module_name = getattr(module, "__name__", "<unknown>")
    task_name = getattr(task, "__name__", "<unknown>")

    async def traced_task(*args, **kwargs):
        with tracer.start_span(
            f"{module_name}.background_task", resource=task_name, child_of=None, activate=True
        ) as span:
            if current_span:
                span.link_span(current_span.context)
            if inspect.iscoroutinefunction(task):
                await task(*args, **kwargs)
            else:
                await run_in_threadpool(task, *args, **kwargs)

    args, kwargs = set_argument_value(args, kwargs, 0, "func", traced_task)
    wrapped(*args, **kwargs)
