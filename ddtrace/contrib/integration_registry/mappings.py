from .utils import get_integration_to_dependency_map, invert_integration_to_dependency_map

EXCLUDED_FROM_TESTING = {"coverage", "pytest_benchmark", "asgi", "wsgi", "boto", "aioredis", "pytest_bdd", "urllib", "webbrowser"}
MODULE_TO_DEPENDENCY_MAPPING = {
    "kafka": "confluent-kafka",
    "consul": "python-consul",
    "snowflake": "snowflake-connector-python",
    "flask_cache": "flask-caching",
    "graphql": "graphql-core",
    "mysql": "mysql-connector-python",
    "mysqldb": "mysqlclient",
    "asyncio": "pytest-asyncio",
    "sqlite3": "pysqlite3-binary",
    "grpc": "grpcio",
    "google_generativeai": "google-generativeai",
    "psycopg2": "psycopg2-binary",
    "cassandra": "cassandra-driver",
    "rediscluster": "redis-py-cluster",
    "dogpile_cache": "dogpile-cache",
    "vertica": "vertica-python",
    "aiohttp_jinja2": "aiohttp-jinja2",
    "azure_functions": "azure-functions",
    "pytest_bdd": "pytest-bdd",
    "aws_lambda": "datadog-lambda",
    "openai_agents": "openai-agents",
}

INTEGRATION_TO_DEPENDENCY_MAPPING = get_integration_to_dependency_map()
DEPENDENCY_TO_INTEGRATION_MAPPING = invert_integration_to_dependency_map(INTEGRATION_TO_DEPENDENCY_MAPPING)


# we need a mapping of dependency name to integration name for collecting versions from riot/requirements, and then saving 
# the versions to supported_versions_output.json


# each integration
# has a name: ie: elasticsearch, psycopg
# - has a dependency name or list of dependency names: ie: ["elasticsearch", "elasticsearch-py" ....], [psycopg2-binary, psycopg2, psycopg]
# -- each dependency name has a module name ie: ["elasticsearch", "elasticsearch-py"], ["psycopg2", "psycopg"] optional if dependency name is the module name
# -- each dependency name has a list of versions that are tested

