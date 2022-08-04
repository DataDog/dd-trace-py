import graphene
import pytest

from ddtrace.contrib.graphql import patch
from ddtrace.contrib.graphql import unpatch
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


@pytest.mark.snapshot(
    ignores=["meta.error.stack"], variants={"v2": graphene.VERSION < (3,), "": graphene.VERSION >= (3,)}
)
def test_schema_failing_execute(failing_schema, test_source_str, enable_graphql_resolvers):
    result = failing_schema.execute(test_source_str)

    assert len(result.errors) == 1
    assert "exception was raised in a graphene query" in str(result.errors[0])
