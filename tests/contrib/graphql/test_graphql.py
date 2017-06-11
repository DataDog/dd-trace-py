# 3p
from nose.tools import eq_, assert_raises, assert_true
from graphql import (
    GraphQLSchema,
    GraphQLObjectType,
    GraphQLField,
    GraphQLString
)
import graphql
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
        print(result.errors)
        span = tracer.writer.pop()[0]
        eq_(span.get_metric(INVALID), int(result.invalid))

    @staticmethod
    def test_request_string_resolve():
        pass

    @staticmethod
    def test_query():
        patch()
        tracer = get_dummy_tracer()
        schema = TracedGraphQLSchema(
          query= GraphQLObjectType(
            name='RootQueryType',
            fields={
              'hello': GraphQLField(
                type= GraphQLString,
                resolver=lambda *_: 'world'
              )
            }
          ),
          datadog_tracer=tracer,
        )
        query = '{ hello world }'
        result = graphql.graphql(schema, query)
        # assert result.data['hello'] == 'world'
        spans = tracer.writer.pop()
        eq_(len(spans), 1)
        s = spans[0]
        eq_(s.get_tag(QUERY), query)
        eq_(s.error, 1)

        unpatch()
        tracer = get_dummy_tracer()
        schema = TracedGraphQLSchema(
          query= GraphQLObjectType(
            name='RootQueryType',
            fields={
              'hello': GraphQLField(
                type= GraphQLString,
                resolver=lambda *_: 'world'
              )
            }
          ),
          datadog_tracer=tracer,
        )
        query = '{ hello }'
        result = graphql.graphql(schema, query)
        spans = tracer.writer.pop()
        assert result.data['hello'] == 'world'
        eq_(len(spans), 0)
