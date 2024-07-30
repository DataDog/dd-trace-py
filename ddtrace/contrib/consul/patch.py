import consul

from ddtrace import config
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...constants import SPAN_KIND
from ...constants import SPAN_MEASURED_KEY
from ...ext import SpanKind
from ...ext import SpanTypes
from ...ext import consul as consulx
from ...ext import net
from ...internal.schema import schematize_service_name
from ...internal.schema import schematize_url_operation
from ...internal.utils import get_argument_value
from ...internal.utils.wrappers import unwrap as _u
from ...pin import Pin


_KV_FUNCS = ["put", "get", "delete"]


def _get_version():
    # type: () -> str
    return getattr(consul, "__version__", "")


def get_version():
    deprecate(
        "get_version is deprecated",
        message="get_version is deprecated",
        removal_version="3.0.0",
        category=DDTraceDeprecationWarning,
    )
    return _get_version()


def patch():
    if getattr(consul, "__datadog_patch", False):
        return
    consul.__datadog_patch = True

    pin = Pin(service=schematize_service_name(consulx.SERVICE))
    pin.onto(consul.Consul.KV)

    for f_name in _KV_FUNCS:
        _w("consul", "Consul.KV.%s" % f_name, _wrap_function(f_name))


def unpatch():
    if not getattr(consul, "__datadog_patch", False):
        return
    consul.__datadog_patch = False

    for f_name in _KV_FUNCS:
        _u(consul.Consul.KV, f_name)


def _wrap_function(name):
    def trace_func(wrapped, instance, args, kwargs):
        pin = Pin.get_from(instance)
        if not pin or not pin.enabled():
            return wrapped(*args, **kwargs)

        # Only patch the synchronous implementation
        if not isinstance(instance.agent.http, consul.std.HTTPClient):
            return wrapped(*args, **kwargs)

        path = get_argument_value(args, kwargs, 0, "key")
        resource = name.upper()

        with pin.tracer.trace(
            schematize_url_operation(consulx.CMD, protocol="http", direction=SpanDirection.OUTBOUND),
            service=pin.service,
            resource=resource,
            span_type=SpanTypes.HTTP,
        ) as span:
            span.set_tag_str(COMPONENT, config.consul.integration_name)

            span.set_tag_str(net.TARGET_HOST, instance.agent.http.host)

            # set span.kind to the type of request being performed
            span.set_tag_str(SPAN_KIND, SpanKind.CLIENT)

            span.set_tag(SPAN_MEASURED_KEY)
            rate = config.consul.get_analytics_sample_rate()
            if rate is not None:
                span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, rate)
            span.set_tag_str(consulx.KEY, path)
            span.set_tag_str(consulx.CMD, resource)
            return wrapped(*args, **kwargs)

    return trace_func


def wrap_function(name):
    deprecate(
        "wrap_function is deprecated",
        message="get_version is deprecated",
        removal_version="3.0.0",
        category=DDTraceDeprecationWarning,
    )
    return _wrap_function(name)
