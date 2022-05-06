import asyncio
import sys

import graphql
import pytest

import ddtrace
from ddtrace.contrib.graphql import patch
from ddtrace.contrib.graphql import unpatch


@pytest.fixture(autouse=True)
def tracer():
    tracer = ddtrace.tracer
    if sys.version_info < (3, 7):
        # enable legacy asyncio support
        from ddtrace.contrib.asyncio.provider import AsyncioContextProvider

        tracer.configure(context_provider=AsyncioContextProvider())

    patch()
    yield tracer
    unpatch()


@pytest.fixture
def test_schema():
    return graphql.GraphQLSchema(
        query=graphql.GraphQLObjectType(
            name="RootQueryType",
            fields={"hello": graphql.GraphQLField(graphql.GraphQLString, resolve=lambda obj, info: "friend")},
        )
    )


@pytest.fixture
def test_schema_async():
    async def async_hello(obj, info):
        await asyncio.sleep(0.1)
        return "async friend"

    return graphql.GraphQLSchema(
        query=graphql.GraphQLObjectType(
            name="RootQueryType",
            fields={"hello": graphql.GraphQLField(graphql.GraphQLString, resolve=async_hello)},
        )
    )


@pytest.fixture
def test_source_str():
    return "{ hello }"


@pytest.fixture
def test_source(test_source_str):
    return graphql.Source(test_source_str)


@pytest.mark.snapshot
@pytest.mark.asyncio
async def test_graphql(test_schema, test_source_str):
    result = await graphql.graphql(test_schema, test_source_str)
    assert result.data == {"hello": "friend"}


@pytest.mark.snapshot
@pytest.mark.asyncio
async def test_graphql_async(tracer, test_schema_async, test_source):
    with tracer.trace("test-async", service="graphql"):
        result = await graphql.graphql(test_schema_async, test_source)
    assert result.data == {"hello": "async friend"}


@pytest.mark.snapshot
def test_graphql_sync(test_schema, test_source_str):
    result = graphql.graphql_sync(test_schema, test_source_str)
    assert result.data == {"hello": "friend"}


@pytest.mark.snapshot
def test_graphql_error(test_schema):
    result = graphql.graphql_sync(test_schema, "{ invalid_schema }")

    assert len(result.errors) == 1
    assert result.errors[0].message == "Cannot query field 'invalid_schema' on type 'RootQueryType'."
