import graphene
import graphql
import pytest

from ddtrace.contrib.internal.graphql.patch import patch
from ddtrace.contrib.internal.graphql.patch import unpatch
from tests.utils import override_config


class Patron(graphene.ObjectType):
    id = graphene.ID()
    name = graphene.String()
    age = graphene.Int()


class PatronQuery(graphene.ObjectType):
    patron = graphene.Field(Patron)

    def resolve_patron(root, info):
        return Patron(id=1, name="Syrus", age=27)


class FailingQuery(graphene.ObjectType):
    patron = graphene.Field(Patron)

    def resolve_patron(root, info):
        raise Exception("exception was raised in a graphene query")


class Query(graphene.ObjectType):
    user = graphene.String(id=graphene.ID())

    def resolve_user(self, info, id):  # noqa: A002
        if id != "123":
            raise graphql.error.GraphQLError(
                "User not found",
                extensions={
                    "code": "USER_NOT_FOUND",
                    "timestamp": "2025-01-30T12:34:56Z",
                    "status": 404,
                    "retryable": False,
                },
            )
        return "John Doe"


@pytest.fixture(autouse=True)
def enable_graphql_patching():
    patch()
    yield
    unpatch()


@pytest.fixture
def test_source_str():
    return """
    {
      patron {
        id
        name
        age
      }
    }
"""


@pytest.fixture
def enable_graphql_resolvers():
    with override_config("graphql", dict(resolvers_enabled=True)):
        yield


@pytest.fixture
def test_schema():
    return graphene.Schema(query=PatronQuery)


@pytest.fixture
def failing_schema():
    return graphene.Schema(query=FailingQuery)


@pytest.mark.snapshot
def test_schema_execute(test_schema, test_source_str):
    result = test_schema.execute(test_source_str)
    assert not result.errors
    assert result.data == {"patron": {"id": "1", "name": "Syrus", "age": 27}}


@pytest.mark.asyncio
@pytest.mark.snapshot(token="tests.contrib.graphene.test_graphene.test_schema_execute")
@pytest.mark.skipif(graphene.VERSION < (3, 0, 0), reason="execute_async is only supported in graphene>=3.0")
async def test_schema_execute_async(test_schema, test_source_str):
    result = await test_schema.execute_async(test_source_str)
    assert not result.errors
    assert result.data == {"patron": {"id": "1", "name": "Syrus", "age": 27}}


@pytest.mark.snapshot
def test_schema_execute_with_resolvers(test_schema, test_source_str, enable_graphql_resolvers):
    result = test_schema.execute(test_source_str)
    assert not result.errors
    assert result.data == {"patron": {"id": "1", "name": "Syrus", "age": 27}}


@pytest.mark.asyncio
@pytest.mark.snapshot(token="tests.contrib.graphene.test_graphene.test_schema_execute_with_resolvers")
@pytest.mark.skipif(graphene.VERSION < (3, 0, 0), reason="execute_async is only supported in graphene>=3.0")
async def test_schema_execute_async_with_resolvers(test_schema, test_source_str, enable_graphql_resolvers):
    result = await test_schema.execute_async(test_source_str)
    assert not result.errors
    assert result.data == {"patron": {"id": "1", "name": "Syrus", "age": 27}}


@pytest.mark.subprocess(env=dict(DD_TRACE_GRAPHQL_ERROR_EXTENSIONS="code, status"))
@pytest.mark.snapshot(ignores=["meta.events", "meta.error.stack"])
def test_schema_failing_extensions(test_schema, test_source_str, enable_graphql_resolvers):
    import ddtrace.auto  # noqa

    import graphene

    from ddtrace.contrib.internal.graphql.patch import patch
    from tests.contrib.graphene.test_graphene import Query

    patch()

    schema = graphene.Schema(query=Query)
    query_string = '{ user(id: "999") }'
    result = schema.execute(query_string)
    assert result.errors


@pytest.mark.snapshot(
    ignores=["meta.error.stack", "meta.events"], variants={"v2": graphene.VERSION < (3,), "": graphene.VERSION >= (3,)}
)
def test_schema_failing_execute(failing_schema, test_source_str, enable_graphql_resolvers):
    result = failing_schema.execute(test_source_str)

    assert len(result.errors) == 1
    assert "exception was raised in a graphene query" in str(result.errors[0])
