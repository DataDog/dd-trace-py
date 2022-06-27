from typing import Any
from typing import Dict
from typing import List
from typing import Optional

import starlette
from starlette.middleware import Middleware
from starlette.routing import Match

from ddtrace import config
from ddtrace.contrib.asgi.middleware import TraceMiddleware
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.span import Span
from ddtrace.vendor.debtcollector import deprecate
from ddtrace.vendor.debtcollector import removals
from ddtrace.vendor.wrapt import ObjectProxy
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w


log = get_logger(__name__)

config._add(
    "starlette",
    dict(
        _default_service="starlette",
        request_span_name="starlette.request",
        distributed_tracing=True,
        aggregate_resources=True,
    ),
)


@removals.remove(removal_version="2.0.0", category=DDTraceDeprecationWarning)
def get_resource(scope):
    path = None
    routes = scope["app"].routes
    for route in routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            path = route.path
            break
        elif match == Match.PARTIAL and path is None:
            path = route.path
    return path


@removals.remove(removal_version="2.0.0", category=DDTraceDeprecationWarning)
def span_modifier(span, scope):
    resource = get_resource(scope)
    if config.starlette["aggregate_resources"] and resource:
        span.resource = "{} {}".format(scope["method"], resource)


def traced_init(wrapped, instance, args, kwargs):
    mw = kwargs.pop("middleware", [])
    mw.insert(0, Middleware(TraceMiddleware, integration_config=config.starlette))
    kwargs.update({"middleware": mw})

    wrapped(*args, **kwargs)


def patch():
    if getattr(starlette, "_datadog_patch", False):
        return

    setattr(starlette, "_datadog_patch", True)

    _w("starlette.applications", "Starlette.__init__", traced_init)

    # We need to check that Fastapi instrumentation hasn't already patched these
    if not isinstance(starlette.routing.Route.handle, ObjectProxy):
        _w("starlette.routing", "Route.handle", traced_handler)
    if not isinstance(starlette.routing.Mount.handle, ObjectProxy):
        _w("starlette.routing", "Mount.handle", traced_handler)


def unpatch():
    if not getattr(starlette, "_datadog_patch", False):
        return

    setattr(starlette, "_datadog_patch", False)

    _u(starlette.applications.Starlette, "__init__")

    # We need to check that Fastapi instrumentation hasn't already unpatched these
    if isinstance(starlette.routing.Route.handle, ObjectProxy):
        _u(starlette.routing.Route, "handle")

    if isinstance(starlette.routing.Mount.handle, ObjectProxy):
        _u(starlette.routing.Mount, "handle")


def traced_handler(wrapped, instance, args, kwargs):
    if config.starlette.get("aggregate_resources") is False or config.fastapi.get("aggregate_resources") is False:
        deprecate(
            "ddtrace.contrib.starlette.patch",
            message="`aggregate_resources` is deprecated and will be removed in tracer version 2.0.0",
            category=DDTraceDeprecationWarning,
        )

        return wrapped(*args, **kwargs)

    # Since handle can be called multiple times for one request, we take the path of each instance
    # Then combine them at the end to get the correct resource names
    scope = get_argument_value(args, kwargs, 0, "scope")  # type: Optional[Dict[str, Any]]
    if not scope:
        return wrapped(*args, **kwargs)

    # Our ASGI TraceMiddleware has not been called, skip since
    # we won't have a request span to attach this information onto
    # DEV: This can happen if patching happens after the app has been created
    if "datadog" not in scope:
        log.warning("datadog context not present in ASGI request scope, trace middleware may be missing")
        return wrapped(*args, **kwargs)

    # Add the path to the resource_paths list
    if "resource_paths" not in scope["datadog"]:
        scope["datadog"]["resource_paths"] = [instance.path]
    else:
        scope["datadog"]["resource_paths"].append(instance.path)

    request_spans = scope["datadog"].get("request_spans", [])  # type: List[Span]
    resource_paths = scope["datadog"].get("resource_paths", [])  # type: List[str]

    if len(request_spans) == len(resource_paths):
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
    # at least always update the root asgi span resource name request_spans[0].resource = "".join(resource_paths)
    elif request_spans and resource_paths:
        if scope.get("method"):
            request_spans[0].resource = "{} {}".format(scope["method"], "".join(resource_paths))
        else:
            request_spans[0].resource = "".join(resource_paths)

    else:
        log.debug(
            "unable to update the request span resource name, request_spans:%r, resource_paths:%r",
            request_spans,
            resource_paths,
        )

    return wrapped(*args, **kwargs)
