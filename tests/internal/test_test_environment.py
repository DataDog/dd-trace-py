from tests.environment import TestEnvironment as Environment
from tests.environment import TestRun as Run


def test_environment_exposes_concrete_execution_metadata():
    environment = Environment(
        id="requests-py311-requests225",
        suite="contrib::requests",
        name="requests",
        python="3.11",
        direct_dependencies=("pytest", "requests~=2.25.0"),
        runs=(Run("pytest tests/contrib/requests", (("DD_TRACE_ENABLED", "true"),)),),
        env=(("REDIS_HOST", "redis"),),
        services=("redis",),
        snapshot=True,
    )

    assert environment.command == "pytest tests/contrib/requests"
    assert environment.environment == {"REDIS_HOST": "redis"}
    assert environment.runs[0].environment == {"DD_TRACE_ENABLED": "true"}
    assert environment.display_name == "Python 3.11, requests~=2.25.0"


def test_environment_display_name_supports_dependency_aliases():
    environment = Environment(
        id="psycopg2-py312",
        suite="contrib::psycopg",
        name="psycopg2",
        python="3.12",
        direct_dependencies=("psycopg2-binary~=2.9.9",),
    )

    assert environment.display_name == "Python 3.12, psycopg2-binary~=2.9.9"
