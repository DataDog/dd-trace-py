import re
from typing import Union

import graphql
from graphql.language.source import Source

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


graphql_version_str = getattr(graphql, "__version__", "0.0.0")
graphql_version = tuple([int(i) for i in graphql_version_str.split(".")])


def patch():
    if getattr(graphql, "_datadog_patch", False):
        return

    setattr(graphql, "_datadog_patch", True)

    if graphql_version < (3, 0):
        _w(graphql, "graphql", _traced_graphql_sync)
    else:
        _w(graphql, "graphql", _traced_graphql_async)
        _w(graphql, "graphql_sync", _traced_graphql_sync)
    Pin().onto(graphql)


def unpatch():
    if not getattr(graphql, "_datadog_patch", False):
        return

    setattr(graphql, "_datadog_patch", False)
    _u(graphql, "graphql")
    if graphql_version >= (3, 0):
        _u(graphql, "graphql_sync")


def _traced_graphql_sync(func, instance, args, kwargs):
    pin = Pin.get_from(graphql)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    resource = _get_source_str(args, kwargs)

    with pin.tracer.trace(
        name=func.__name__,
        resource=resource,
        service=trace_utils.int_service(pin, config.graphql),
        span_type=SpanTypes.WEB,
    ) as span:
        _init_span(span)
        result = func(*args, **kwargs)
        return result


async def _traced_graphql_async(func, instance, args, kwargs):
    pin = Pin.get_from(graphql)
    if not pin or not pin.enabled():
        return await func(*args, **kwargs)

    resource = _get_source_str(args, kwargs)

    with pin.tracer.trace(
        name=func.__name__,
        resource=resource,
        service=trace_utils.int_service(pin, config.graphql),
        span_type=SpanTypes.WEB,
    ) as span:
        _init_span(span)
        return await func(*args, **kwargs)


def _init_span(span):
    # type: (Span) -> None
    span.set_tag(SPAN_MEASURED_KEY)

    sample_rate = config.graphql.get_analytics_sample_rate()
    if sample_rate is not None:
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, sample_rate)


def _get_source_str(f_args, f_kwargs):
    # type (Any, Any) -> str
    source = get_argument_value(f_args, f_kwargs, 1, "source")  # type: Union[str, Source]
    if isinstance(source, Source):
        source_str = source.body
    else:
        source_str = source
    # remove new lines, tabs and extra whitespace from source_str
    return re.sub(r"\s+", " ", source_str).strip()
