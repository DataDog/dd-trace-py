# 3p
from nose.tools import eq_, assert_raises
from graphql import (
    GraphQLSchema,
    GraphQLObjectType,
    GraphQLField,
    GraphQLString
)
import graphql

# project
from ddtrace.ext import errors
from tests.test_tracer import get_dummy_tracer
import ddtrace
from ddtrace.contrib.graphql import patch, unpatch, QUERY, TracedGraphQLSchema, traced_graphql



class TestGraphQL(object):

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
