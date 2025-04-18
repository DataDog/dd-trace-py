from .utils import get_integration_to_dependency_map, invert_integration_to_dependency_map

EXCLUDED_FROM_TESTING = {"coverage", "pytest_benchmark", "asgi", "wsgi", "boto", "aioredis", "pytest_bdd", "urllib", "webbrowser"}
INTEGRATION_TO_DEPENDENCY_MAPPING_SPECIAL_CASES = {
    "flask_cache": "flask-caching",
    "asyncio": "pytest-asyncio",
    "sqlite3": "pysqlite3-binary",
    "botocore": "boto3",
    # "psycopg2": "psycopg2-binary",
    "aiohttp_jinja2": "aiohttp-jinja2",
    "pytest_bdd": "pytest-bdd",
    "aws_lambda": "datadog-lambda",
}

INTEGRATION_TO_DEPENDENCY_MAPPING = get_integration_to_dependency_map(INTEGRATION_TO_DEPENDENCY_MAPPING_SPECIAL_CASES)
DEPENDENCY_TO_INTEGRATION_MAPPING = invert_integration_to_dependency_map(INTEGRATION_TO_DEPENDENCY_MAPPING)
