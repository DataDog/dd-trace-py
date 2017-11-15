# 3p
from nose.tools import eq_, assert_raises, assert_true
from graphql import (
    GraphQLSchema,
    GraphQLObjectType,
    GraphQLField,
    GraphQLString
)
import graphql
from graphql.language.source import Source as GraphQLSource
from graphql.language.parser import parse as graphql_parse
from wrapt import FunctionWrapper

# project
import ddtrace
from ddtrace.contrib.graphql import (
    TracedGraphQLSchema,
    patch, unpatch, traced_graphql,
    QUERY, ERRORS, INVALID
)
from tests.test_tracer import get_dummy_tracer


def get_traced_schema(tracer=None, query=None):
    tracer = tracer or get_dummy_tracer()
    query = query or GraphQLObjectType(
        name='RootQueryType',
        fields={
            'hello': GraphQLField(
                type=GraphQLString,
                resolver=lambda *_: 'world'
            )
        }
    )
    return tracer, TracedGraphQLSchema(query=query, datadog_tracer=tracer)


class TestGraphQL(object):

    @staticmethod
    def test_unpatch():
        gql = graphql.graphql
        unpatch()
        eq_(gql, graphql.graphql)
        assert_true(not isinstance(graphql.graphql, FunctionWrapper))
        patch()
        assert_true(isinstance(graphql.graphql, FunctionWrapper))
        unpatch()
        eq_(gql, graphql.graphql)

    @staticmethod
    def test_invalid():
        tracer, schema = get_traced_schema()
        result = traced_graphql(schema, '{ hello world }')
        span = tracer.writer.pop()[0]
        eq_(span.get_metric(INVALID), int(result.invalid))

        result = traced_graphql(schema, '{ hello }')
        span = tracer.writer.pop()[0]
        eq_(span.get_metric(INVALID), int(result.invalid))

    @staticmethod
    def test_request_string_resolve():
        query = '{ hello }'

        # string as args[1]
        tracer, schema = get_traced_schema()
        result = traced_graphql(schema, query)
        span = tracer.writer.pop()[0]
        eq_(span.get_tag(QUERY), query)

        # string as kwargs.get('request_string')
        tracer, schema = get_traced_schema()
        result = traced_graphql(schema, request_string=query)
        span = tracer.writer.pop()[0]
        eq_(span.get_tag(QUERY), query)

        # ast as args[1]
        tracer, schema = get_traced_schema()
        ast_query = graphql_parse(GraphQLSource(query, 'Test Request'))
        result = traced_graphql(schema, ast_query)
        span = tracer.writer.pop()[0]
        eq_(span.get_tag(QUERY), query)

        # ast as kwargs.get('request_string')
        tracer, schema = get_traced_schema()
        ast_query = graphql_parse(GraphQLSource(query, 'Test Request'))
        result = traced_graphql(schema, request_string=ast_query)
        span = tracer.writer.pop()[0]
        eq_(span.get_tag(QUERY), query)

    @staticmethod
    def test_query_tag():
        query = '{ hello }'
        tracer, schema = get_traced_schema()
        result = traced_graphql(schema, query)
        span = tracer.writer.pop()[0]
        eq_(span.get_tag(QUERY), query)

        # test query also for error span, just in case
        query = '{ hello world }'
        tracer, schema = get_traced_schema()
        result = traced_graphql(schema, query)
        span = tracer.writer.pop()[0]
        eq_(span.get_tag(QUERY), query)

    @staticmethod
    def test_errors_tag():
        query = '{ hello world }'
        tracer, schema = get_traced_schema()
        result = traced_graphql(schema, query)
        span = tracer.writer.pop()[0]
        assert_true(span.get_tag(ERRORS))
        eq_(str(result.errors), span.get_tag(ERRORS))
