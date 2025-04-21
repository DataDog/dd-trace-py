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
DEPENDENCY_TO_INTEGRATION_MAPPING_SPECIAL_CASES = {
    "flask-caching": "flask-cache",
    "pytest-asyncio": "asyncio",
    "pysqlite3-binary": "sqlite3",
    "dogpile.cache": "dogpile_cache",
    "dogpile_cache": "dogpile_cache",
    "dogpile-cache": "dogpile_cache",
    "boto3": "boto",
    "pytest-bdd": "pytest_bdd",
    "datadog-lambda": "aws_lambda",
    "datadog_lambda": "aws_lambda",
    "aiohttp-jinja2": "aiohttp_jinja2",
    "aiohttp_jinja2": "aiohttp_jinja2"
}

INTEGRATION_TO_DEPENDENCY_MAPPING = get_integration_to_dependency_map(DEPENDENCY_TO_INTEGRATION_MAPPING_SPECIAL_CASES)
DEPENDENCY_TO_INTEGRATION_MAPPING = invert_integration_to_dependency_map(INTEGRATION_TO_DEPENDENCY_MAPPING)
