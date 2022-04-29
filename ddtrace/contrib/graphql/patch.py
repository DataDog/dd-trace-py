from typing import Union

import graphql
from graphql.language import Source

from ddtrace import Span
from ddtrace import config
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.pin import Pin
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

from .. import trace_utils
from ...ext import SpanTypes


config._add("graphql", dict(_default_service="graphql"))
graphql_version = getattr(graphql, "version_info", "0.0.0")


def patch():
    if getattr(graphql, "_datadog_patch", False) or graphql_version < (1, 1):
        return

    setattr(graphql, "_datadog_patch", True)
    _w(graphql, "graphql", _traced_graphql)
    _w(graphql, "graphql_sync", _traced_graphql_sync)
    Pin().onto(graphql)


def unpatch():
    if not getattr(graphql, "_datadog_patch", False) or graphql_version < (1, 1):
        return

    setattr(graphql, "_datadog_patch", False)
    _u(graphql, "graphql")
    _u(graphql, "graphql_sync")


async def _traced_graphql(func, instance, args, kwargs):
    return await _traced_graphql_sync(func, instance, args, kwargs)


def _traced_graphql_sync(func, instance, args, kwargs):
    pin = Pin.get_from(graphql)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    source = get_argument_value(args, kwargs, 1, "source")  # type: Union[str, Source]
    if isinstance(source, Source):
        resource = source.body
    else:
        resource = source

    with pin.tracer.trace(
        name=func.__name__,
        resource=resource,
        service=trace_utils.ext_service(pin, config.graphql),
        span_type=SpanTypes.WEB,
    ) as span:
        _init_span(span)
        try:
            return func(*args, **kwargs)
        except Exception:
            span.set_traceback()
            raise


def _init_span(span):
    # type: (Span) -> None
    span.set_tag(SPAN_MEASURED_KEY)

    sample_rate = config.graphql.get_analytics_sample_rate()
    if sample_rate is not None:
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, sample_rate)
