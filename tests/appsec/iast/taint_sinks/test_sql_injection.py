import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking import taint_pyobject
from ddtrace.appsec._iast.constants import VULN_SQL_INJECTION
from ddtrace.appsec._iast.taint_sinks._base import VulnerabilityBase
from ddtrace.internal import core
from tests.appsec.iast.aspects.conftest import _iast_patched_module
from tests.appsec.iast.iast_utils import get_line_and_hash


DDBBS = [
    (
        "tests/appsec/iast/fixtures/taint_sinks/sql_injection_sqlite3.py",
        "tests.appsec.iast.fixtures.taint_sinks.sql_injection_sqlite3",
    ),
    (
        "tests/appsec/iast/fixtures/taint_sinks/sql_injection_psycopg2.py",
        "tests.appsec.iast.fixtures.taint_sinks.sql_injection_psycopg2",
    ),
    (
        "tests/appsec/iast/fixtures/taint_sinks/sql_injection_sqlalchemy.py",
        "tests.appsec.iast.fixtures.taint_sinks.sql_injection_sqlalchemy",
    ),
]


@pytest.mark.parametrize("fixture_path,fixture_module", DDBBS)
def test_sql_injection(fixture_path, fixture_module, iast_span_defaults):
    mod = _iast_patched_module(fixture_module)
    table = taint_pyobject(
        pyobject="students",
        source_name="test_ossystem",
        source_value="students",
        source_origin=OriginType.PARAMETER,
    )
    assert is_pyobject_tainted(table)

    mod.sqli_simple(table)
    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    assert span_report
    data = span_report.build_and_scrub_value_parts()
    vulnerability = data["vulnerabilities"][0]
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
    assert vulnerability["hash"] == hash_value


@pytest.mark.parametrize("fixture_path,fixture_module", DDBBS)
def test_sql_injection_deduplication(fixture_path, fixture_module, iast_span_deduplication_enabled):
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

    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_deduplication_enabled)

    assert span_report
    data = span_report.build_and_scrub_value_parts()
    assert len(data["vulnerabilities"]) == 1
    VulnerabilityBase._prepare_report._reset_cache()
