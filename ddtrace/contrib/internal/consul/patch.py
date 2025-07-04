from typing import Dict

import consul
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.ext import consul as consulx
from ddtrace.ext import net
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.trace import Pin


_KV_FUNCS = ["put", "get", "delete"]


def get_version():
    # type: () -> str
    return getattr(consul, "__version__", "")


def _supported_versions() -> Dict[str, str]:
    return {"consul": ">=1.1"}


def patch():
    if getattr(consul, "__datadog_patch", False):
        return
    consul.__datadog_patch = True

    pin = Pin(service=schematize_service_name(consulx.SERVICE))
    pin.onto(consul.Consul.KV)

    for f_name in _KV_FUNCS:
        _w("consul", "Consul.KV.%s" % f_name, wrap_function(f_name))


def unpatch():
    if not getattr(consul, "__datadog_patch", False):
        return
    consul.__datadog_patch = False

    for f_name in _KV_FUNCS:
        _u(consul.Consul.KV, f_name)


def wrap_function(name):
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

            span.set_tag(_SPAN_MEASURED_KEY)
            span.set_tag_str(consulx.KEY, path)
            span.set_tag_str(consulx.CMD, resource)
            return wrapped(*args, **kwargs)

    return trace_func
