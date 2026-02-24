import os
from urllib.parse import urlencode

import molten
import wrapt
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.contrib import trace_utils
from ddtrace.contrib._events.web_framework import WebRequestEvent
from ddtrace.contrib.internal.trace_utils import unwrap as _u
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.importlib import func_name

from .wrappers import WrapperComponent
from .wrappers import WrapperMiddleware
from .wrappers import WrapperRenderer
from .wrappers import WrapperRouter


config._add(
    "molten",
    dict(
        _default_service=schematize_service_name("molten"),
        distributed_tracing=asbool(os.getenv("DD_MOLTEN_DISTRIBUTED_TRACING", default=True)),
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

    _w(molten.BaseApp, "__init__", patch_app_init)
    _w(molten.App, "__call__", patch_app_call)


def unpatch():
    if getattr(molten, "_datadog_patch", False):
        molten._datadog_patch = False

        _u(molten.BaseApp, "__init__")
        _u(molten.App, "__call__")


def patch_app_call(wrapped, instance, args, kwargs):
    # DEV: This is safe because this is the args for a WSGI handler
    #   https://www.python.org/dev/peps/pep-3333/
    environ, start_response = args

    request = molten.http.Request.from_environ(environ)

    with core.context_with_event(
        WebRequestEvent(
            url_operation="molten.request",
            component=config.molten.integration_name,
            service=trace_utils.int_service(None, config.molten),
            resource=func_name(wrapped),
            allow_default_resource=True,
            integration_config=config.molten,
            activate_distributed_headers=True,
            request_headers=dict(request.headers),
            request_method=request.method,
            request_url="%s://%s:%s%s" % (request.scheme, request.host, request.port, request.path),
            request_query=urlencode(dict(request.params)),
            tags={"molten.version": molten.__version__},
        )
    ) as ctx:
        ctx.set_item("headers_case_sensitive", True)

        @wrapt.function_wrapper
        def _w_start_response(wrapped, instance, args, kwargs):
            status, headers, exc_info = args
            code, _, _ = status.partition(" ")
            ctx.event.response_headers = dict(headers)
            try:
                ctx.event.response_status_code = int(code)
            except ValueError:
                ctx.event.response_status_code = None

            return wrapped(*args, **kwargs)

        # patching for extracting response code
        start_response = _w_start_response(start_response)

        return wrapped(environ, start_response, **kwargs)


def patch_app_init(wrapped, instance, args, kwargs):
    # allow instance to be initialized before wrapping them
    wrapped(*args, **kwargs)

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
