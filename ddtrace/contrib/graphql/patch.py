import os
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
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.version import parse_version
from ddtrace.pin import Pin
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

from .. import trace_utils
from ...ext import SpanTypes


config._add("graphql", dict(_default_service="graphql"))


graphql_version_str = getattr(graphql, "__version__")
graphql_version = parse_version(graphql_version_str)

if graphql_version < (3, 0):
    from graphql.language.ast import Document
else:
    from graphql.language.ast import DocumentNode as Document


def patch():
    if getattr(graphql, "_datadog_patch", False) or graphql_version < (2, 0):
        return

    if graphql_version < (2, 1):
        # Patch functions used in graphql.graphql()
        _w("graphql.graphql", "execute_graphql", _traced_operation("graphql.query"))
        _w("graphql.graphql", "parse", _traced_operation("graphql.parse"))
        _w("graphql.graphql", "validate", _traced_operation("graphql.validate"))
        _w("graphql.graphql", "execute", _traced_operation("graphql.execute"))
        # Patch execute functions exposed in the public api
        _w("graphql.execution", "execute", _traced_operation("graphql.execute"))
        _w("graphql", "execute", _traced_operation("graphql.execute"))
    elif graphql_version < (3, 0):
        # Patch functions used in graphql.graphql()
        _w("graphql.graphql", "execute_graphql", _traced_operation("graphql.query"))
        _w("graphql.backend.core", "parse", _traced_operation("graphql.parse"))
        _w("graphql.backend.core", "validate", _traced_operation("graphql.validate"))
        _w("graphql.backend.core", "execute", _traced_operation("graphql.execute"))
        _w("graphql.execution.executor", "execute", _traced_operation("graphql.execute"))
        # Patch execute functions exposed in the public api
        _w("graphql.execution", "execute", _traced_operation("graphql.execute"))
        _w("graphql", "execute", _traced_operation("graphql.execute"))
    else:
        # Patch functions used in graphql.graphql and graphql.graphql_sync
        _w("graphql.graphql", "graphql_impl", _traced_operation("graphql.query"))
        _w("graphql.graphql", "parse", _traced_operation("graphql.parse"))
        _w("graphql.validation", "validate", _traced_operation("graphql.validate"))
        _w("graphql.graphql", "execute", _traced_operation("graphql.execute"))
        # Patch execute functions exposed in the public api
        _w("graphql.execution", "execute", _traced_operation("graphql.execute"))
        _w("graphql", "execute", _traced_operation("graphql.execute"))
        if graphql_version > (3, 1):
            _w("graphql", "execute_sync", _traced_operation("graphql.execute"))
            _w("graphql.execution", "execute_sync", _traced_operation("graphql.execute"))

    _patch_resolvers()

    setattr(graphql, "_datadog_patch", True)
    Pin().onto(graphql)


def unpatch():
    pass


def _patch_resolvers():
    if not asbool(os.getenv("DD_TRACE_GRAPHQL_PATCH_RESOLVERS", default=True)):
        return
    elif graphql_version < (3, 0):
        _w("graphql.execution.executor", "resolve_field", _traced_operation("graphql.resolve"))
    elif graphql_version < (3, 2):
        _w("graphql.execution.execute", "ExecutionContext.resolve_field", _traced_operation("graphql.resolve"))
    else:
        # ExecutionContext.resolve_field was renamed to execute_field in graphql-core 3.2
        _w("graphql.execution.execute", "ExecutionContext.execute_field", _traced_operation("graphql.resolve"))


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
    if span_name == "graphql.query":
        return _get_source_from_query(f_args, f_kwargs)
    elif span_name == "graphql.execute":
        return _get_source_from_execute(f_args, f_kwargs)
    elif span_name == "graphql.resolve":
        return _get_resolver_field_name(f_args, f_kwargs)
    return span_name


def _init_span(span):
    # type: (Span) -> None
    span.set_tag(SPAN_MEASURED_KEY)

    sample_rate = config.graphql.get_analytics_sample_rate()
    if sample_rate is not None:
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, sample_rate)


def _get_source_from_query(f_args, f_kwargs):
    # type: (Any, Any) -> str
    source = get_argument_value(f_args, f_kwargs, 1, "source")  # type: Union[Document, str, Source]
    source_str = ""
    if isinstance(source, Source):
        source_str = source.body
    elif isinstance(source, str):
        source_str = source
    else:  # Document
        source_str = source.loc.source.body
    # remove new lines, tabs and extra whitespace from source_str
    return re.sub(r"\s+", " ", source_str).strip()


def _get_source_from_execute(f_args, f_kwargs):
    # type: (Any, Any) -> str
    if graphql_version < (3, 0):
        document = get_argument_value(f_args, f_kwargs, 1, "document_ast")
    else:
        document = get_argument_value(f_args, f_kwargs, 1, "document")

    source_str = document.loc.source.body
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
    if isinstance(result, list) and result and isinstance(result[0], GraphQLError):
        # graphql.valdidate spans wraps functions which returns a list of GraphQLErrors
        errors = result
    elif isinstance(result, ExecutionResult) and result.errors:
        # graphql.execute and graphql.query wrap an ExecutionResult
        # which contains a list of errors
        errors = result.errors
    else:
        # do nothing for wrapped functions which do not return a list of errors
        return

    error_msgs = "\n".join([stringify(error) for error in errors])
    span.set_exc_fields(GraphQLError, error_msgs, "")
