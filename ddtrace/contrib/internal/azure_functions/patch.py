import azure.functions as azure_functions
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.trace_utils import int_service
from ddtrace.contrib.trace_utils import set_http_meta
from ddtrace.contrib.trace_utils import unwrap as _u
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.compat import parse
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema import schematize_cloud_faas_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.pin import Pin


AAS_FUNCTION_NAME = "aas.function.name"  # codespell:ignore
AAS_FUNCTION_TRIGGER = "aas.function.trigger"  # codespell:ignore

config._add(
    "azure_functions",
    {
        "_default_service": schematize_service_name("azure_functions"),
    },
)


def get_version():
    # type: () -> str
    return getattr(azure_functions, "__version__", "")


def _set_span_meta(span, req, res, function_name, path, trigger):
    span.set_tag_str(AAS_FUNCTION_NAME, function_name)
    span.set_tag_str(AAS_FUNCTION_TRIGGER, trigger)

    set_http_meta(
        span=span,
        integration_config=config.azure_functions,
        method=req.method,
        url=req.url,
        request_headers=req.headers,
        response_headers=res.headers if res else None,
        route=path,
        status_code=res.status_code if res else None,
    )


def patch():
    """
    Patch `azure.functions` module for tracing
    """
    # Check to see if we have patched azure.functions yet or not
    if getattr(azure_functions, "_datadog_patch", False):
        return
    azure_functions._datadog_patch = True

    Pin().onto(azure_functions.FunctionApp)
    _w("azure.functions", "FunctionApp.route", _patched_route)


def _patched_route(wrapped, instance, args, kwargs):
    trigger = "Http"

    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    def _wrapper(func):
        function_name = func.__name__

        def wrap_function(
            req: azure_functions.HttpRequest, context: azure_functions.Context
        ) -> azure_functions.HttpResponse:
            parsed_url = parse.urlparse(req.url)
            path = parsed_url.path
            resource = f"{req.method} {path}"

            operation_name = schematize_cloud_faas_operation(
                "azure.functions.invoke", cloud_provider="azure", cloud_service="functions"
            )
            with pin.tracer.trace(
                operation_name,
                service=int_service(pin, config.azure_functions),
                resource=resource,
                span_type=SpanTypes.SERVERLESS,
            ) as span:
                span.set_tag_str(COMPONENT, config.azure_functions.integration_name)
                span.set_tag_str(SPAN_KIND, SpanKind.SERVER)

                res = None
                try:
                    res = func(req)
                    return res
                finally:
                    _set_span_meta(span, req, res, function_name, path, trigger)

        # Needed to correctly display function name when running 'func start' locally
        wrap_function.__name__ = function_name

        return wrapped(*args, **kwargs)(wrap_function)

    return _wrapper


def unpatch():
    if not getattr(azure_functions, "_datadog_patch", False):
        return
    azure_functions._datadog_patch = False

    _u(azure_functions.FunctionApp, "route")
