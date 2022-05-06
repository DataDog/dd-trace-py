from typing import Union

import graphql
from graphql.language import Source

from ddtrace import Span
from ddtrace import config
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.internal.compat import stringify
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
    _w(graphql, "graphql_sync", _traced_graphql_sync)
    _w(graphql, "graphql", _traced_graphql)
    Pin().onto(graphql)


def unpatch():
    if not getattr(graphql, "_datadog_patch", False) or graphql_version < (1, 1):
        return

    setattr(graphql, "_datadog_patch", False)
    _u(graphql, "graphql")
    _u(graphql, "graphql_sync")


def _traced_graphql_sync(func, instance, args, kwargs):
    pin = Pin.get_from(graphql)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    resource = _get_source(args, kwargs)

    with pin.tracer.trace(
        name="graphql_sync",
        resource=resource,
        service=trace_utils.ext_service(pin, config.graphql),
        span_type=SpanTypes.WEB,
    ) as span:
        _init_span(span)
        result = func(*args, **kwargs)
        _set_span_errors(result, span)
        return result


async def _traced_graphql(func, instance, args, kwargs):
    pin = Pin.get_from(graphql)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    resource = _get_source(args, kwargs)

    with pin.tracer.trace(
        name="graphql",
        resource=resource,
        service=trace_utils.ext_service(pin, config.graphql),
        span_type=SpanTypes.WEB,
    ) as span:
        _init_span(span)
        result = await func(*args, **kwargs)
        _set_span_errors(result, span)
        return result


def _init_span(span):
    # type: (Span) -> None
    span.set_tag(SPAN_MEASURED_KEY)

    sample_rate = config.graphql.get_analytics_sample_rate()
    if sample_rate is not None:
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, sample_rate)


def _get_source(f_args, f_kwargs):
    # type (Any, Any) -> str
    source = get_argument_value(f_args, f_kwargs, 1, "source")  # type: Union[str, Source]
    if isinstance(source, Source):
        return source.body
    else:
        return source


def _set_span_errors(result, span):
    # type (graphql.ExecutionResult, Span) -> None
    if not result.errors:
        return

    # Set first error in the ExecutionResult on the Span
    exception = result.errors[0]
    span.set_exc(type(exception), exception, getattr(exception, "__traceback__") or "")

    for i in range(1, len(result.errors)):
        error_msg = stringify(result.errors[i])
        span.set_tag("other_errors.%d.*" % (i,), error_msg)
