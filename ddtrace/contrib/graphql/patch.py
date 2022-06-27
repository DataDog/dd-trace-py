from functools import partial
import os
import re
import sys
from typing import Any

from ddtrace import Span
from ddtrace import config
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.internal.compat import stringify
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.packages import get_distributions
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.version import parse_version
from ddtrace.pin import Pin

from .. import trace_utils
from ...ext import SpanTypes


config._add("graphql", dict(_default_service="graphql"))
_patch_resolvers = asbool(os.getenv("DD_GRAPHQL_PATCH_RESOLVERS", default=True))


def patch():
    if "graphql" in sys.modules.keys():
        # patching only works if graphql-core is not imported
        return

    if not ModuleWatchdog.is_installed():
        ModuleWatchdog.install()

    for dist in get_distributions():
        graphql_version = parse_version(dist.version)
        if (dist.name != "graphql" and dist.name != "graphql-core") or graphql_version < (2, 0):
            continue

        to_patch = [
            ("graphql/graphql.py", "graphql.query", "graphql_impl"),
            ("graphql/language/parser.py", "graphql.parse", "parse"),
            ("graphql/validation/validate.py", "graphql.validate", "validate"),
            ("graphql/execution/execute.py", "graphql.execute", "execute"),
        ]
        if graphql_version < (3, 0):
            to_patch = [
                ("graphql/graphql.py", "graphql.query", "execute_graphql"),
                ("graphql/language/parser.py", "graphql.parse", "parse"),
                ("graphql/validation/validation.py", "graphql.validate", "validate"),
                ("graphql/execution/executor.py", "graphql.execute", "execute"),
            ]

        for module, span_name, patch_method in to_patch:
            path_to_module = os.path.join(dist.path, module)
            hook = partial(_traced_operation, span_name, patch_method, graphql_version)
            ModuleWatchdog.register_origin_hook(path_to_module, hook)

    import graphql

    Pin().onto(graphql)
    setattr(graphql, "_datadog_patch", True)


def unpatch():
    pass


def _traced_operation(span_name, method, graphql_version, module):
    func = getattr(module, method)

    def _wrapper(*args, **kwargs):
        import graphql

        pin = Pin.get_from(graphql)
        if not pin or not pin.enabled():
            return func(*args, **kwargs)

        if span_name == "graphql.execute" and _patch_resolvers:
            middleware = _get_middleware_func(pin)
            args, kwargs = _inject_trace_middleware_to_args(middleware, graphql_version, args, kwargs)

        resource = _get_resource(span_name, graphql_version, args, kwargs)
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

    setattr(module, method, _wrapper)


def _get_middleware_func(pin):
    # Note - middleware can not be a partial. It must be a class or a function
    def _middleware(next_middleware, root, info, **args):
        with pin.tracer.trace(
            name="graphql.resolve",
            resource=info.field_name,
            service=trace_utils.int_service(pin, config.graphql),
            span_type=SpanTypes.WEB,
        ) as span:
            _init_span(span)
            return next_middleware(root, info, **args)

    return _middleware


def _inject_trace_middleware_to_args(middleware, graphql_version, args, kwargs):
    middlewares_arg = 8
    if graphql_version > (3, 2):
        middlewares_arg = 9

    if len(args) > middlewares_arg:
        middlewares = args[middlewares_arg] or []
    elif "middleware" in kwargs:
        middlewares = kwargs["middleware"] or []
    else:
        middlewares = []

    if hasattr(middlewares, "middlewares"):
        middlewares = middlewares.middlewares

    middlewares = [middleware] + list(middlewares)

    if len(args) > middlewares_arg:
        args = args[0:middlewares_arg] + (middlewares,) + args[middlewares_arg + 1 :]
    else:
        kwargs["middleware"] = middlewares

    return args, kwargs


def _get_resource(span_name, graphql_version, f_args, f_kwargs):
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
    if isinstance(obj, str):
        source_str = obj
    elif hasattr(obj, "body") and isinstance(obj.body, str):  # graphql.Source object
        source_str = obj.body
    elif hasattr(obj, "loc") and hasattr(obj.loc, "source"):  # Document
        source_str = obj.loc.source.body
    else:
        source_str = ""
    # remove new lines, tabs and extra whitespace from source_str
    return re.sub(r"\s+", " ", source_str).strip()


def _init_span(span):
    # type: (Span) -> None
    span.set_tag(SPAN_MEASURED_KEY)

    sample_rate = config.graphql.get_analytics_sample_rate()
    if sample_rate is not None:
        span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, sample_rate)


def _set_span_errors(result, span):
    # type: (Any, Span) -> None
    from graphql.error import GraphQLError

    if isinstance(result, list) and result and isinstance(result[0], GraphQLError):
        # graphql.valdidate spans wraps functions which returns a list of GraphQLErrors
        errors = result
    elif hasattr(result, "errors") and result.errors:
        # graphql.execute and graphql.query wrap an ExecutionResult
        # which contains a list of errors
        errors = result.errors
    else:
        # do nothing for wrapped functions which do not return a list of errors
        return

    error_msgs = "\n".join([stringify(error) for error in errors])
    span.set_exc_fields(GraphQLError, error_msgs, "")
