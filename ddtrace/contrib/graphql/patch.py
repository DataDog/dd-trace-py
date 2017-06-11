"""
Tracing for the graphql-core library.

https://github.com/graphql-python/graphql-core
"""

# stdlib
import logging

# 3p
import wrapt
import graphql
from graphql.language.ast import Document

# project
import ddtrace
from ddtrace.util import unwrap


logger = logging.getLogger(__name__)

_graphql = graphql.graphql

TYPE = 'graphql'
SERVICE = 'graphql'
QUERY = 'graphql.query'
ERRORS = 'graphql.errors'
INVALID = 'graphql.invalid'
RES = 'graphql.graphql'


class TracedGraphQLSchema(graphql.GraphQLSchema):
    def __init__(self, *args, **kwargs):
        if 'datadog_tracer' in kwargs:
            self.datadog_tracer = kwargs.pop('datadog_tracer')
            logger.debug(
                'For schema %s using own tracer %s',
                self, self.datadog_tracer)
        super(TracedGraphQLSchema, self).__init__(*args, **kwargs)


def patch():
    """Monkeypatch the graphql-core library to trace graphql calls execution."""
    logger.debug('Patching `graphql.graphql` function.')
    wrapt.wrap_function_wrapper(graphql, 'graphql', _traced_graphql)


def unpatch():
    logger.debug('Unpatching `graphql.graphql` function.')
    unwrap(graphql, 'graphql')


def _traced_graphql(func, _, args, kwargs):
    """Wrapper for graphql.graphql function."""

    schema = args[0]

    # get the query as a string
    if len(args) > 1:
        request_string = args[1]
    else:
        request_string = kwargs.get('request_string')

    if isinstance(request_string, Document):
        query = request_string.loc.source.body
    else:
        query = request_string

    # allow schamas their own tracer with fallback to the global
    tracer = getattr(schema, 'datadog_tracer', ddtrace.tracer)

    if not tracer.enabled:
        return func(*args, **kwargs)

    with tracer.trace(RES, span_type=TYPE, service=SERVICE,) as span:
        span.set_tag(QUERY, query)
        result = None
        try:
            result = func(*args, **kwargs)
            return result
        finally:
            # `span.error` must be integer
            span.error = int(result is None or result.invalid)
            if result is not None:
                span.set_tag(ERRORS, result.errors)
                span.set_tag(INVALID, result.invalid)
            else:
                logger.debug(
                    'Uncaught exception occured during graphql execution.',
                    exc_info=True)


def traced_graphql(*args, **kwargs):
    return _traced_graphql(_graphql, None, args, kwargs)
