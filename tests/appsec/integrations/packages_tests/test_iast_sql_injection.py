import pytest

from ddtrace import patch
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast.constants import VULN_SQL_INJECTION
from ddtrace.appsec._iast.taint_sinks._base import VulnerabilityBase
from tests.appsec.iast.iast_utils import _iast_patched_module
from tests.appsec.iast.iast_utils import get_line_and_hash
from tests.appsec.iast.taint_sinks.conftest import _get_iast_data


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
    assert vulnerability["location"]["class"] == ""
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
