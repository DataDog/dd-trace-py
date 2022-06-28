import os
import re
import sys
from typing import Any
from typing import Callable
from typing import Dict
from typing import Iterable
from typing import Tuple
from typing import Union

import graphql
from graphql import MiddlewareManager
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
from ddtrace.internal.wrapping import unwrap
from ddtrace.internal.wrapping import wrap
from ddtrace.pin import Pin

from .. import trace_utils
from ...ext import SpanTypes


graphql_version_str = getattr(graphql, "__version__")
graphql_version = parse_version(graphql_version_str)

if graphql_version < (3, 0):
    from graphql.language.ast import Document
else:
    from graphql.language.ast import DocumentNode as Document

config._add("graphql", dict(_default_service="graphql"))
# Patching resolvers can produce a large trace, this environment variable will allow
# users to disable this instrumentation
_patch_resolvers = asbool(os.getenv("DD_TRACE_GRAPHQL_PATCH_RESOLVERS", default=True))


def patch():
    if getattr(graphql, "_datadog_patch", False) or graphql_version < (2, 0):
        return
    setattr(graphql, "_datadog_patch", True)
    Pin().onto(graphql)

    if graphql_version < (3, 0):
        to_patch = [
            ("graphql.graphql", "graphql.query", "execute_graphql"),
            ("graphql.language.parser", "graphql.parse", "parse"),
            ("graphql.validation.validation", "graphql.validate", "validate"),
            ("graphql.execution.executor", "graphql.execute", "execute"),
        ]
    else:
        to_patch = [
            ("graphql.graphql", "graphql.query", "graphql_impl"),
            ("graphql.language.parser", "graphql.parse", "parse"),
            ("graphql.validation.validate", "graphql.validate", "validate"),
            ("graphql.execution.execute", "graphql.execute", "execute"),
        ]

    for module_str, span_name, patch_method in to_patch:
        module = sys.modules[module_str]
        func = getattr(module, patch_method)
        wrapper = _traced_operation(span_name)
        # wrap(...) updates the behavior of the original function.
        # All references to the function will be patched.
        wrap(func, wrapper)


def unpatch():
    if not getattr(graphql, "_datadog_patch", False) or graphql_version < (2, 0):
        return

    if graphql_version < (3, 0):
        patched = [
            ("graphql.graphql", "execute_graphql"),
            ("graphql.language.parser", "parse"),
            ("graphql.validation.validation", "validate"),
            ("graphql.execution.executor", "execute"),
        ]
    else:
        patched = [
            ("graphql.graphql", "graphql_impl"),
            ("graphql.language.parser", "parse"),
            ("graphql.validation.validate", "validate"),
            ("graphql.execution.execute", "execute"),
        ]

    for module_str, patch_method in patched:
        module = sys.modules[module_str]
        func = getattr(module, patch_method)
        unwrap(func)

    setattr(graphql, "_datadog_patch", False)


def _traced_operation(span_name):
    # type: (str) -> Callable
    """
    Returns a wrapper for graphql operations.
    Supports: graphql.graphql() via execute_graphql/graphql_impl,
    graphql.parse(), graphql.validate(), and graphql.execute()
    """

    def _wrapper(func, args, kwargs):
        pin = Pin.get_from(graphql)
        if not pin or not pin.enabled():
            return func(*args, **kwargs)

        if span_name == "graphql.execute" and _patch_resolvers:
            # patch resolvers
            middleware = _get_middleware_func(pin)
            args, kwargs = _inject_trace_middleware_to_args(middleware, args, kwargs)

        # set resource name
        resource = _get_resource(span_name, args, kwargs)

        with pin.tracer.trace(
            name=span_name,
            resource=resource,
            service=trace_utils.int_service(pin, config.graphql),
            span_type=SpanTypes.WEB,
        ) as span:
            # mark span as measured and set sample rate
            span.set_tag(SPAN_MEASURED_KEY)
            sample_rate = config.graphql.get_analytics_sample_rate()
            if sample_rate is not None:
                span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, sample_rate)
            result = func(*args, **kwargs)
            # set error tags if the result contains a list of GraphqlErrors
            _set_span_errors(result, span)
            return result

    return _wrapper


def _get_middleware_func(pin):
    # type: (Pin) -> Callable
    def resolver_middleware(next_middleware, root, info, **args):
        """
        trace middleware which wraps the resolvers of graphql fields.
        Note - graphql middlewares can not be a partial. It must be a class or a function.
        """
        with pin.tracer.trace(
            name="graphql.resolve",
            resource=info.field_name,
            service=trace_utils.int_service(pin, config.graphql),
            span_type=SpanTypes.WEB,
        ):
            return next_middleware(root, info, **args)

    return resolver_middleware


def _inject_trace_middleware_to_args(trace_middleware, args, kwargs):
    # type: (Callable, Tuple, Dict) -> Tuple[Tuple, Dict]
    """
    Intercepts and appends a middleware to graphql.execute(...) arguments
    """
    middlewares_arg = 8
    if graphql_version >= (3, 2):
        # middleware is the 10th argument graphql.execute(..) version 3.2+
        middlewares_arg = 9

    # get middlewares from args or kwargs
    # if the middlewares is empty or None then set to empty list
    if len(args) > middlewares_arg and args[middlewares_arg]:
        middlewares = args[middlewares_arg]  # type: Union[Iterable, MiddlewareManager]
    elif "middleware" in kwargs and kwargs["middleware"]:
        middlewares = kwargs["middleware"]  # type: Union[Iterable, MiddlewareManager]
    else:
        middlewares = []
    # if middleware is a MiddlewareManager get iterable from field
    if isinstance(middlewares, MiddlewareManager):
        middlewares = middlewares.middlewares  # type: Iterable

    # add trace_middleware to the list of execute middlewares
    middlewares = [trace_middleware] + list(middlewares)

    # update args and kwargs to contain trace_middleware
    if len(args) > middlewares_arg:
        args = args[0:middlewares_arg] + (middlewares,) + args[middlewares_arg + 1 :]
    else:
        kwargs["middleware"] = middlewares

    return args, kwargs


def _get_resource(span_name, f_args, f_kwargs):
    # type: (str, Tuple, Dict) -> str
    """
    Gets graphql source string from query and execute operations.
    Otherwise returns the span name (span.resource = span.name).
    """
    if span_name == "graphql.query":
        source = get_argument_value(f_args, f_kwargs, 1, "source")
        return _get_source_str(source)
    elif span_name == "graphql.execute" and graphql_version < (3, 0):
        document = get_argument_value(f_args, f_kwargs, 1, "document_ast")
        return _get_source_str(document)
    elif span_name == "graphql.execute":
        document = get_argument_value(f_args, f_kwargs, 1, "document")
        return _get_source_str(document)
    return span_name


def _get_source_str(obj):
    # type: (Union[str, Source, Document]) -> str
    """
    Parses graphql Documents and Source objects to retrieve
    the graphql source input for a request.
    """
    if isinstance(obj, str):
        source_str = obj
    elif isinstance(obj, Source):
        source_str = obj.body
    elif isinstance(obj, Document):
        source_str = obj.loc.source.body
    else:
        source_str = ""
    # remove new lines, tabs and extra whitespace from source_str
    return re.sub(r"\s+", " ", source_str).strip()


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
