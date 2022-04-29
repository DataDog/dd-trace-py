import re
from typing import Any
from typing import Union

import graphql
from graphql.error import GraphQLError
from graphql.execution import ExecutionResult
from graphql.language.source import Source

from ddtrace import Span
from ddtrace import config
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.internal.compat import stringify
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.version import parse_version
from ddtrace.pin import Pin
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

from .. import trace_utils
from ...ext import SpanTypes


config._add("graphql", dict(_default_service="graphql"))


graphql_version_str = getattr(graphql, "__version__", "0.0.0")
graphql_version = parse_version(graphql_version_str)


def patch():
    if getattr(graphql, "_datadog_patch", False) or graphql_version < (2, 0):
        return

    setattr(graphql, "_datadog_patch", True)

    graphql_module = "graphql.graphql"
    graphql_func = "graphql_impl"
    if graphql_version < (3, 0):
        graphql_module = "graphql"
        graphql_func = "graphql"

    parse_execute_module = "graphql.graphql"
    if (2, 1) <= graphql_version < (3, 0):
        parse_execute_module = "graphql.backend.core"

    validate_module = "graphql.validation"
    if graphql_version < (2, 1):
        validate_module = "graphql.graphql"
    elif (2, 1) <= graphql_version < (3, 0):
        validate_module = "graphql.backend.core"

    resolve_module = "graphql.execution.execute"
    # ExecutionContext.resolve_field was renamed to execute_field in graphql-core 3.2
    resolve_func = "ExecutionContext.execute_field"
    if graphql_version < (3, 0):
        resolve_module = "graphql.execution.executor"
        resolve_func = "resolve_field"
    elif graphql_version < (3, 2):
        resolve_func = "ExecutionContext.resolve_field"

    _w(graphql_module, graphql_func, _traced_operation("graphql.query"))
    _w(parse_execute_module, "parse", _traced_operation("graphql.parse"))
    _w(validate_module, "validate", _traced_operation("graphql.validate"))
    _w(parse_execute_module, "execute", _traced_operation("graphql.execute"))
    _w(resolve_module, resolve_func, _traced_operation("graphql.resolve"))

    Pin().onto(graphql)


def unpatch():
    pass


def _traced_operation(span_name):
    def _wrapper(func, instance, args, kwargs):
        pin = Pin.get_from(graphql)
        if not pin or not pin.enabled():
            return func(*args, **kwargs)

        resource = _get_resource(span_name, args, kwargs)
        with pin.tracer.trace(
            name=span_name,
            resource=resource,
            service=trace_utils.int_service(pin, config.graphql),
            span_type=SpanTypes.WEB,
        ) as span:
            _init_span(span)
            result = func(*args, **kwargs)
            _set_span_errors(result, span)
            return result

    return _wrapper


def _get_resource(span_name, f_args, f_kwargs):
    if span_name == "graphql.resolve":
        return _get_resolver_field_name(f_args, f_kwargs)
    elif span_name == "graphql.query":
        return _get_source_str(f_args, f_kwargs)
    return span_name


def _init_span(span):
    # type: (Span) -> None
    span.set_tag(SPAN_MEASURED_KEY)

    sample_rate = config.graphql.get_analytics_sample_rate()
    if sample_rate is not None:
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, sample_rate)


def _get_source_str(f_args, f_kwargs):
    # type: (Any, Any) -> str
    source = get_argument_value(f_args, f_kwargs, 1, "source")  # type: Union[str, Source]
    if isinstance(source, Source):
        source_str = source.body
    else:
        source_str = source
    # remove new lines, tabs and extra whitespace from source_str
    return re.sub(r"\s+", " ", source_str).strip()


def _get_resolver_field_name(f_args, f_kwargs):
    # type: (Any, Any) -> str
    fields_arg = 2
    fields_kw = "field_nodes"
    if graphql_version < (3, 0):
        fields_arg = 3
        fields_kw = "field_asts"
    fields_def = get_argument_value(f_args, f_kwargs, fields_arg, fields_kw)
    # field definitions should never be null/empty. A field must exist before
    # a graphql execution context attempts to resolve a query.
    # Only the first field is resolved:
    # https://github.com/graphql-python/graphql-core/blob/v3.0.0/src/graphql/execution/execute.py#L586-L593
    return fields_def[0].name.value


def _set_span_errors(result, span):
    # type: (Any, Span) -> None
    if not isinstance(result, ExecutionResult) or not result.errors:
        return

    error_msgs = ""
    for error in result.errors:
        error_msgs = "%s\n%s" % (error_msgs, stringify(error))
    span.set_exc_fields(GraphQLError, error_msgs.strip(), "")
