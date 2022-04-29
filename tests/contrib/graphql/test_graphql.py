import asyncio
import sys

import graphql
import pytest

import ddtrace
from ddtrace.contrib.graphql import patch
from ddtrace.contrib.graphql import unpatch
from tests.utils import DummyTracer
from tests.utils import TracerSpanContainer


@pytest.fixture
def tracer():
    original_tracer = ddtrace.tracer
    tracer = DummyTracer()
    if sys.version_info < (3, 7):
        # enable legacy asyncio support
        from ddtrace.contrib.asyncio.provider import AsyncioContextProvider

        tracer.configure(context_provider=AsyncioContextProvider())

    setattr(ddtrace, "tracer", tracer)
    patch()
    yield tracer
    setattr(ddtrace, "tracer", original_tracer)
    unpatch()


@pytest.fixture
def test_spans(tracer):
    container = TracerSpanContainer(tracer)
    yield container
    container.reset()


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
        return "friend"

    return graphql.GraphQLSchema(
        query=graphql.GraphQLObjectType(
            name="RootQueryType",
            fields={"hello": graphql.GraphQLField(graphql.GraphQLString, resolve=async_hello)},
        )
    )


@pytest.fixture
def test_source():
    return "{ hello }"


def test_graphql_sync(test_spans, test_schema, test_source):
    result = graphql.graphql_sync(test_schema, test_source)
    assert result.data == {"hello": "friend"}

    spans = test_spans.get_spans()
    assert len(spans) == 1
    assert spans[0].name == "graphql_sync"
    assert spans[0].resource == test_source
    assert spans[0].service == "graphql"


@pytest.mark.asyncio
async def test_graphql_async(test_spans, test_schema_async, test_source):
    result = await graphql.graphql(test_schema_async, test_source)
    assert result.data == {"hello": "friend"}

    spans = test_spans.get_spans()
    assert len(spans) == 1
    assert spans[0].name == "graphql"
    assert spans[0].resource == test_source
    assert spans[0].service == "graphql"
