from typing import Any
from typing import Callable
from typing import Optional
from typing import cast
from urllib.parse import urlencode

import molten
import wrapt
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib import trace_utils
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.settings import env
from ddtrace.internal.span_bus import span_from_context
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.importlib import func_name

from .wrappers import MOLTEN_REQUEST_EVENT_KEY
from .wrappers import WrapperComponent
from .wrappers import WrapperMiddleware
from .wrappers import WrapperRenderer
from .wrappers import WrapperRouter


config._add(
    "molten",
    dict(
        _default_service=schematize_service_name("molten"),
        distributed_tracing=asbool(env.get("DD_MOLTEN_DISTRIBUTED_TRACING", default=True)),
    ),
)


def get_version() -> str:
    return getattr(molten, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"molten": ">=1.0"}


def patch():
    if getattr(molten, "_datadog_patch", False):
        return
    molten._datadog_patch = True

    pin = Pin()

    pin.onto(molten)

    _w(molten.BaseApp, "__init__", patch_app_init)
    _w(molten.App, "__call__", patch_app_call)


def unpatch():
    if getattr(molten, "_datadog_patch", False):
        molten._datadog_patch = False

        pin = Pin.get_from(molten)
        if pin:
            pin.remove_from(molten)

        _u(molten.BaseApp, "__init__")
        _u(molten.App, "__call__")


def _parse_status_code(status: str) -> Optional[int]:
    code, _, _ = status.partition(" ")
    try:
        return int(code)
    except (TypeError, ValueError):
        return None


def patch_app_call(wrapped, instance, args, kwargs):
    pin = Pin.get_from(molten)

    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    # DEV: This is safe because this is the args for a WSGI handler
    #   https://www.python.org/dev/peps/pep-3333/
    environ, start_response = args

    request = molten.http.Request.from_environ(environ)
    request_headers = dict(request.headers)

    url = "%s://%s:%s%s" % (
        request.scheme,
        request.host,
        request.port,
        request.path,
    )

    query = urlencode(dict(request.params))

    event = WebFrameworkRequestEvent(
        http_operation="molten.request",
        component=config.molten.integration_name,
        integration_config=config.molten,
        service=trace_utils.int_service(pin, config.molten),
        resource=func_name(wrapped),
        tags={"molten.version": get_version()},
        request_method=request.method,
        request_url=url,
        request_headers=request_headers,
        query=query,
        request_route=None,
        allow_default_resource=True,
        activate_distributed_headers=True,
        headers_case_sensitive=True,
    )

    with core.context_with_event(event) as ctx:
        ctx.set_item("req_span", span_from_context(ctx))
        ctx.set_item(MOLTEN_REQUEST_EVENT_KEY, event)

        @wrapt.function_wrapper
        def _w_start_response(wrapped, instance, args, kwargs):
            status = args[0]
            event.response_status_code = _parse_status_code(status)
            if event.set_resource:
                event.resource = None
            return wrapped(*args, **kwargs)

        start_response_wrapper = cast(Callable[..., Any], _w_start_response)
        traced_response = start_response_wrapper(start_response)
        return wrapped(environ, traced_response, **kwargs)


def patch_app_init(wrapped, instance, args, kwargs):
    # allow instance to be initialized before wrapping them
    wrapped(*args, **kwargs)

    # add Pin to instance
    pin = Pin.get_from(molten)

    if not pin or not pin.enabled():
        return

    # Wrappers here allow us to trace objects without altering class or instance
    # attributes, which presents a problem when classes in molten use
    # ``__slots__``

    instance.router = WrapperRouter(instance.router)

    # wrap middleware functions/callables
    instance.middleware = [WrapperMiddleware(mw) for mw in instance.middleware]

    # wrap components objects within injector
    # NOTE: the app instance also contains a list of components but it does not
    # appear to be used for anything passing along to the dependency injector
    instance.injector.components = [WrapperComponent(c) for c in instance.injector.components]

    # but renderers objects
    instance.renderers = [WrapperRenderer(r) for r in instance.renderers]
