import os

import pytest

from ddtrace.appsec._iast.taint_sinks.code_injection import patch as code_injection_patch
from ddtrace.contrib.internal.psycopg.patch import patch as psycopg_patch
from ddtrace.contrib.internal.psycopg.patch import unpatch as psycopg_unpatch
from ddtrace.contrib.internal.sqlalchemy.patch import patch as sqlalchemy_patch
from ddtrace.contrib.internal.sqlalchemy.patch import unpatch as sqlalchemy_unpatch
from ddtrace.contrib.internal.sqlite3.patch import patch as sqli_sqlite_patch
from ddtrace.contrib.internal.sqlite3.patch import unpatch as sqli_sqlite_unpatch
from tests.appsec.iast.iast_utils import _end_iast_context_and_oce
from tests.appsec.iast.iast_utils import _start_iast_context_and_oce
from tests.contrib.config import MYSQL_CONFIG, POSTGRES_CONFIG
from tests.utils import override_global_config


@pytest.fixture(autouse=True)
def iast_create_context():
    # Ensure DB credentials are available for package integration tests across CI environments
    # without embedding secrets in source.
    if not (os.getenv("TEST_POSTGRES_USER") or os.getenv("PGUSER") or os.getenv("POSTGRES_USER")):
        os.environ["PGUSER"] = str(POSTGRES_CONFIG.get("user", ""))
    if not (os.getenv("TEST_POSTGRES_PASSWORD") or os.getenv("PGPASSWORD") or os.getenv("POSTGRES_PASSWORD")):
        os.environ["PGPASSWORD"] = str(POSTGRES_CONFIG.get("password", ""))
    if not (os.getenv("TEST_POSTGRES_DB") or os.getenv("PGDATABASE") or os.getenv("POSTGRES_DB")):
        os.environ["PGDATABASE"] = str(POSTGRES_CONFIG.get("dbname", "postgres"))
    if not (os.getenv("TEST_POSTGRES_PORT") or os.getenv("PGPORT")) and POSTGRES_CONFIG.get("port"):
        os.environ["PGPORT"] = str(POSTGRES_CONFIG.get("port"))

    if not (os.getenv("TEST_MYSQL_USER") or os.getenv("MYSQL_USER")):
        os.environ["MYSQL_USER"] = str(MYSQL_CONFIG.get("user", ""))
    if not (os.getenv("TEST_MYSQL_PASSWORD") or os.getenv("MYSQL_PASSWORD") or os.getenv("MYSQL_PWD")):
        os.environ["MYSQL_PASSWORD"] = str(MYSQL_CONFIG.get("password", ""))
    if not (os.getenv("TEST_MYSQL_DB") or os.getenv("TEST_MYSQL_DATABASE") or os.getenv("MYSQL_DATABASE")):
        os.environ["MYSQL_DATABASE"] = str(MYSQL_CONFIG.get("database", "test"))
    if not (os.getenv("TEST_MYSQL_PORT") or os.getenv("MYSQL_PORT")) and MYSQL_CONFIG.get("port"):
        os.environ["MYSQL_PORT"] = str(MYSQL_CONFIG.get("port"))
    with override_global_config(
        dict(_iast_enabled=True, _iast_deduplication_enabled=False, _iast_request_sampling=100.0)
    ):
        sqlalchemy_patch()
        psycopg_patch()
        sqli_sqlite_patch()
        code_injection_patch()
        _start_iast_context_and_oce()
        try:
            yield
        finally:
            _end_iast_context_and_oce()
            psycopg_unpatch()
            sqlalchemy_unpatch()
            sqli_sqlite_unpatch()
