import pytest

from ddtrace.contrib.internal.psycopg.patch import patch as psycopg_patch
from ddtrace.contrib.internal.psycopg.patch import unpatch as psycopg_unpatch
from ddtrace.contrib.internal.sqlalchemy.patch import patch as sqlalchemy_patch
from ddtrace.contrib.internal.sqlalchemy.patch import unpatch as sqlalchemy_unpatch
from ddtrace.contrib.internal.sqlite3.patch import patch as sqli_sqlite_patch
from ddtrace.contrib.internal.sqlite3.patch import unpatch as sqli_sqlite_unpatch
from tests.appsec.iast.conftest import _end_iast_context_and_oce
from tests.appsec.iast.conftest import _start_iast_context_and_oce
from tests.utils import override_global_config


@pytest.fixture(autouse=True)
def iast_create_context():
    with override_global_config(
        dict(_iast_enabled=True, _iast_deduplication_enabled=False, _iast_request_sampling=100.0)
    ):
        sqlalchemy_patch()
        psycopg_patch()
        sqli_sqlite_patch()
        _start_iast_context_and_oce()
        yield
        _end_iast_context_and_oce()
        psycopg_unpatch()
        sqlalchemy_unpatch()
        sqli_sqlite_unpatch()
