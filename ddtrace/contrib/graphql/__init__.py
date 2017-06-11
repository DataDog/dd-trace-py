"""
To trace all GraphQL requests, patch the library like so::

    from ddtrace.contrib.graphql import patch
    patch()

    from graphql import graphql
    result = graphql(schema, query)


If you do not want to monkeypatch ``graphql.graphql`` function or want to trace
only certain calls you can use the ``traced_graphql`` function::

    from ddtrace.contrib.graphql import traced_graphql
    traced_graphql(schema, query)
"""


from ..util import require_modules

required_modules = ['graphql']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import (
            TracedGraphQLSchema,
            patch, unpatch, traced_graphql,
            TYPE, SERVICE, QUERY, ERRORS, INVALID, RES
        )
        __all__ = [
            'TracedGraphQLSchema',
            'patch', 'unpatch', 'traced_graphql',
            'TYPE', 'SERVICE', 'QUERY', 'ERRORS', 'INVALID', 'RES'
        ]
