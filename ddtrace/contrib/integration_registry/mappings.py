from .utils import get_integration_to_dependency_map
from .utils import invert_integration_to_dependency_map


EXCLUDED_FROM_TESTING = {
    "coverage",
    "pytest_benchmark",
    "asgi",
    "wsgi",
    "boto",
    "aioredis",
    "pytest_bdd",
    "urllib",
    "webbrowser",
}
INTEGRATION_TO_DEPENDENCY_MAPPING_SPECIAL_CASES = {
    "flask_cache": "flask-caching",
    "asyncio": "pytest-asyncio",
    "sqlite3": "pysqlite3-binary",
    "botocore": "boto3",
    "pytest_bdd": "pytest-bdd",
    "aws_lambda": "datadog-lambda", # datadog_lambda can be installed as datadog-lambda or datadog_lambda
    "aiohttp_jinja2": "aiohttp_jinja2" # aiohttp_jinja2 can be installed as aiohttp-jinja2 or aiohttp_jinja2
}

INTEGRATION_TO_DEPENDENCY_MAPPING = get_integration_to_dependency_map(INTEGRATION_TO_DEPENDENCY_MAPPING_SPECIAL_CASES)
DEPENDENCY_TO_INTEGRATION_MAPPING = invert_integration_to_dependency_map(INTEGRATION_TO_DEPENDENCY_MAPPING)
