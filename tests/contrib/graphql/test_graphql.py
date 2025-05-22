import os

import graphql
from graphql import build_schema
from graphql import graphql_sync
import pytest

from ddtrace.contrib.internal.graphql.patch import _graphql_version as graphql_version
from ddtrace.contrib.internal.graphql.patch import patch
from ddtrace.contrib.internal.graphql.patch import unpatch
from ddtrace.trace import tracer
from tests.utils import override_config
from tests.utils import snapshot


@pytest.fixture(autouse=True)
def enable_graphql_patching():
    patch()
    yield
    unpatch()


@pytest.fixture
def enable_graphql_resolvers():
    with override_config("graphql", dict(resolvers_enabled=True)):
        yield


@pytest.fixture
def test_schema():
    return graphql.GraphQLSchema(
        query=graphql.GraphQLObjectType(
            name="RootQueryType",
            fields={"hello": graphql.GraphQLField(graphql.GraphQLString, None, lambda obj, info: "friend")},
        )
    )


@pytest.fixture
def test_source_str():
    return "query HELLO { hello }"


@pytest.fixture
def test_middleware():
    def dummy_middleware(next_middleware, root, info, **args):
        with tracer.trace("test_middleware"):
            return next_middleware(root, info, **args)

    yield dummy_middleware


@pytest.fixture
def test_source(test_source_str):
    return graphql.Source(test_source_str)


@pytest.mark.asyncio
async def test_graphql(test_schema, test_source_str, snapshot_context):
    with snapshot_context():
        if graphql_version < (3, 0):
            result = graphql.graphql(test_schema, test_source_str)
        else:
            result = await graphql.graphql(test_schema, test_source_str)
        assert result.data == {"hello": "friend"}


@pytest.mark.asyncio
async def test_graphql_with_traced_resolver(test_schema, test_source_str, snapshot_context, enable_graphql_resolvers):
    with snapshot_context():
        if graphql_version < (3, 0):
            result = graphql.graphql(test_schema, test_source_str)
        else:
            result = await graphql.graphql(test_schema, test_source_str)
        assert result.data == {"hello": "friend"}


def resolve_fail(root, info):
    undefined_var = None
    return undefined_var.property


@snapshot(
    ignores=["meta.error.stack", "meta.events"],
    variants={"2.x": graphql_version >= (3, 0), "3.x": graphql_version < (3, 0)},
)
def test_graphql_fail(enable_graphql_resolvers):
    query = """
    query {
      fail
    }
    """

    resolvers = {
        "Query": {
            "fail": resolve_fail,
        }
    }
    schema_definition = """
    type Query {
        fail: String
    }
    """

    test_schema = build_schema(schema_definition)
    result = graphql_sync(
        test_schema, query, root_value=None, field_resolver=lambda _type, _field: resolvers[_type.name][_field.name]
    )

    assert result.errors is not None
    assert len(result.errors) == 1
    assert isinstance(result.errors[0], graphql.error.GraphQLError)
    assert "'NoneType' object has no attribute 'name'" in result.errors[0].message


@pytest.mark.asyncio
async def test_graphql_error(test_schema, snapshot_context):
    with snapshot_context(ignores=["meta.error.type", "meta.error.message", "meta.events"]):
        if graphql_version < (3, 0):
            result = graphql.graphql(test_schema, "query my_query{ invalid_schema }")
        else:
            result = await graphql.graphql(test_schema, "query my_query{ invalid_schema }")
        assert len(result.errors) == 1
        assert isinstance(result.errors[0], graphql.error.GraphQLError)
        assert "Cannot query field" in result.errors[0].message


@snapshot(token_override="tests.contrib.graphql.test_graphql.test_graphql")
@pytest.mark.skipif(graphql_version >= (3, 0), reason="graphql version>=3.0 does not return a promise")
def test_graphql_v2_promise(test_schema, test_source_str):
    promise = graphql.graphql(test_schema, test_source_str, return_promise=True)
    result = promise.get()
    assert result.data == {"hello": "friend"}


@snapshot(
    token_override="tests.contrib.graphql.test_graphql.test_graphql_error",
    ignores=["meta.error.type", "meta.error.message"],
)
@pytest.mark.skipif(graphql_version >= (3, 0), reason="graphql.graphql is NOT async in v2.0")
def test_graphql_error_v2_promise(test_schema):
    promise = graphql.graphql(test_schema, "query my_query{ invalid_schema }", return_promise=True)
    result = promise.get()
    assert len(result.errors) == 1
    assert isinstance(result.errors[0], graphql.error.GraphQLError)
    assert result.errors[0].message == 'Cannot query field "invalid_schema" on type "RootQueryType".'


@snapshot()
@pytest.mark.skipif(graphql_version >= (3, 0), reason="graphql.graphql is NOT async in v2.0")
def test_graphql_v2_with_document(test_schema, test_source_str):
    source = graphql.language.source.Source(test_source_str, "GraphQL request")
    document_ast = graphql.language.parser.parse(source)
    result = graphql.graphql(test_schema, document_ast)
    assert result.data == {"hello": "friend"}


@snapshot()
def test_graphql_with_document_with_no_location(test_schema, test_source_str):
    source = graphql.language.source.Source(test_source_str, "GraphQL request")
    document_ast = graphql.language.parser.parse(source, no_location=True)
    result = graphql.execute(test_schema, document_ast)
    assert result.data == {"hello": "friend"}


@snapshot(token_override="tests.contrib.graphql.test_graphql.test_graphql")
@pytest.mark.skipif(graphql_version < (3, 0), reason="graphql.graphql_sync is NOT supported in v2.0")
def test_graphql_sync(test_schema, test_source_str):
    result = graphql.graphql_sync(test_schema, test_source_str)
    assert result.data == {"hello": "friend"}


@snapshot()
def test_graphql_execute_with_middleware(test_schema, test_source_str, test_middleware, enable_graphql_resolvers):
    with tracer.trace("test-execute-instrumentation"):
        source = graphql.language.source.Source(test_source_str, "GraphQL request")
        ast = graphql.language.parser.parse(source)
        # execute() can be imported from two modules, ensure both are patched
        res1 = graphql.execute(test_schema, ast, middleware=[test_middleware])
        res2 = graphql.execution.execute(test_schema, ast, middleware=[test_middleware])
        assert res1.data == {"hello": "friend"}
        assert res2.data == {"hello": "friend"}


@snapshot(token_override="tests.contrib.graphql.test_graphql.test_graphql_execute_with_middleware")
@pytest.mark.skipif(graphql_version < (3, 1), reason="graphql.execute_sync is not supported in graphql<3.1")
def test_graphql_execute_sync_with_middlware_manager(
    test_schema, test_source_str, test_middleware, enable_graphql_resolvers
):
    with tracer.trace("test-execute-instrumentation"):
        source = graphql.language.source.Source(test_source_str, "GraphQL request")
        ast = graphql.language.parser.parse(source)
        middleware_manager = graphql.MiddlewareManager(test_middleware)
        # execute_sync() can be imported from two modules, ensure both are patched
        res1 = graphql.execute_sync(test_schema, ast, middleware=middleware_manager)
        res2 = graphql.execution.execute_sync(test_schema, ast, middleware=middleware_manager)
        assert res1.data == {"hello": "friend"}
        assert res2.data == {"hello": "friend"}


@snapshot(ignores=["meta.error.stack", "meta.events"])
def test_regression_graphql_error_locations_none():
    """
    Ensure GraphQLError with `locations=None` does not cause patching issues.
    """
    error = graphql.GraphQLError(message="Test error with no locations")
    error.locations = None
    result = graphql.ExecutionResult(data=None, errors=[error])

    assert result.errors[0].locations is None


@pytest.mark.snapshot
@pytest.mark.skipif(graphql_version < (3, 0), reason="graphql.graphql_sync is NOT supported in v2.0")
@pytest.mark.parametrize(
    "service_name, schema_version",
    [(None, None), ("my-service", None), (None, "v0"), ("my-service", "v0"), (None, "v1"), ("my-service", "v1")],
)
def test_span_schematization(ddtrace_run_python_code_in_subprocess, schema_version, service_name):
    code = """
import sys
import pytest
import graphql
from tests.contrib.graphql.test_graphql import test_schema
from tests.contrib.graphql.test_graphql import test_source_str
from tests.contrib.graphql.test_graphql import enable_graphql_patching

def test(test_schema, test_source_str):
    result = graphql.graphql_sync(test_schema, test_source_str)
    assert result.data == {"hello": "friend"}

if __name__ == "__main__":
    sys.exit(pytest.main(["-x", __file__]))
    """

    env = os.environ.copy()
    if service_name:
        env["DD_SERVICE"] = service_name
    if schema_version:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema_version
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, out.decode()
    assert err == b"", err.decode()
