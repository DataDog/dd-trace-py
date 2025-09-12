import pytest

from ddtrace import patch
from ddtrace.appsec._iast import load_iast
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted
from ddtrace.appsec._iast.constants import VULN_SQL_INJECTION
from ddtrace.appsec._iast.taint_sinks._base import VulnerabilityBase
from tests.appsec.iast.iast_utils import _get_iast_data
from tests.appsec.iast.iast_utils import _iast_patched_module
from tests.appsec.iast.iast_utils import get_line_and_hash


DDBBS = [
    (
        "tests/appsec/integrations/fixtures/sql_injection_sqlite3.py",
        "tests.appsec.integrations.fixtures.sql_injection_sqlite3",
    ),
    (
        "tests/appsec/integrations/fixtures/sql_injection_mysqldb.py",
        "tests.appsec.integrations.fixtures.sql_injection_mysqldb",
    ),
    (
        "tests/appsec/integrations/fixtures/sql_injection_pymysql.py",
        "tests.appsec.integrations.fixtures.sql_injection_pymysql",
    ),
    (
        "tests/appsec/integrations/fixtures/sql_injection_psycopg2.py",
        "tests.appsec.integrations.fixtures.sql_injection_psycopg2",
    ),
    (
        "tests/appsec/integrations/fixtures/sql_injection_sqlalchemy.py",
        "tests.appsec.integrations.fixtures.sql_injection_sqlalchemy",
    ),
]


def setup_module():
    patch(pymysql=True, mysqldb=True)
    load_iast()


@pytest.mark.parametrize("fixture_path,fixture_module", DDBBS)
def test_sql_injection(fixture_path, fixture_module):
    mod = _iast_patched_module(fixture_module)
    table = taint_pyobject(
        pyobject="students",
        source_name="test_ossystem",
        source_value="students",
        source_origin=OriginType.PARAMETER,
    )
    assert is_pyobject_tainted(table)

    mod.sqli_simple(table)

    data = _get_iast_data()
    assert len(data["vulnerabilities"]) >= 1
    # We will pick up weak hash vulnerabilities in some db connector libraries
    # but we are only interested in SQL Injection vulnerabilities
    sqli_vulnerabilities = [x for x in data["vulnerabilities"] if x["type"] == VULN_SQL_INJECTION]
    assert len(sqli_vulnerabilities) == 1
    vulnerability = sqli_vulnerabilities[0]
    source = data["sources"][0]
    assert vulnerability["type"] == VULN_SQL_INJECTION
    assert vulnerability["evidence"]["valueParts"] == [
        {"value": "SELECT "},
        {"redacted": True},
        {"value": " FROM "},
        {"value": "students", "source": 0},
    ]
    assert "value" not in vulnerability["evidence"].keys()
    assert source["name"] == "test_ossystem"
    assert source["origin"] == OriginType.PARAMETER
    assert source["value"] == "students"

    line, hash_value = get_line_and_hash("test_sql_injection", VULN_SQL_INJECTION, filename=fixture_path)
    assert vulnerability["location"]["path"] == fixture_path
    assert vulnerability["location"]["line"] == line
    assert vulnerability["location"]["method"] == "sqli_simple"
    assert "class" not in vulnerability["location"]
    assert vulnerability["hash"] == hash_value


@pytest.mark.parametrize("fixture_path,fixture_module", DDBBS)
def test_sql_injection_deduplication(fixture_path, fixture_module):
    mod = _iast_patched_module(fixture_module)

    table = taint_pyobject(
        pyobject="students",
        source_name="test_ossystem",
        source_value="students",
        source_origin=OriginType.PARAMETER,
    )
    assert is_pyobject_tainted(table)
    for _ in range(0, 5):
        mod.sqli_simple(table)

    data = _get_iast_data()
    # We will pick up weak hash vulnerabilities in some db connector libraries
    # but we are only interested in SQL Injection vulnerabilities
    sqli_vulnerabilities = [x for x in data["vulnerabilities"] if x["type"] == VULN_SQL_INJECTION]
    assert len(sqli_vulnerabilities) == 1
    VulnerabilityBase._prepare_report._reset_cache()


def test_sqlalchemy_complex_query_error():
    """Test that complex SQLAlchemy queries with CASE expressions work correctly with IAST.

    This test verifies that the IAST system properly handles complex SQLAlchemy queries
    that use CASE expressions. The test was added to catch a specific issue where
    SQLAlchemy's internal objects would raise a SystemError when their __repr__ was called
    during string formatting operations.

    The error occurred because some SQLAlchemy objects (like those in CASE expressions)
    have a __repr__ method that can raise exceptions in certain contexts. When IAST tried
    to format these objects as part of its taint tracking, it would encounter the error:

        SystemError: <slot wrapper '__repr__' of 'object' objects> returned a result with an exception set

    This was fixed by improving the string formatting logic in the IAST modulo aspect to
    properly handle objects with problematic __repr__ methods.
    """
    mod = _iast_patched_module("tests.appsec.integrations.fixtures.sql_injection_sqlalchemy")

    result = mod.sql_complex()
    assert result == [("1",)]
