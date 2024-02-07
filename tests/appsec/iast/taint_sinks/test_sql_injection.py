import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking import taint_pyobject
from ddtrace.appsec._iast.constants import VULN_SQL_INJECTION
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

    assert len(span_report.vulnerabilities) == 1
    vulnerability = list(span_report.vulnerabilities)[0]
    source = span_report.sources[0]
    assert vulnerability.type == VULN_SQL_INJECTION
    assert vulnerability.evidence.valueParts == [{"value": "SELECT 1 FROM "}, {"source": 0, "value": "students"}]
    assert vulnerability.evidence.value is None
    assert vulnerability.evidence.pattern is None
    assert vulnerability.evidence.redacted is None
    assert source.name == "test_ossystem"
    assert source.origin == OriginType.PARAMETER
    assert source.value == "students"

    line, hash_value = get_line_and_hash("test_sql_injection", VULN_SQL_INJECTION, filename=fixture_path)
    assert vulnerability.location.line == line
    assert vulnerability.location.path == fixture_path
    assert vulnerability.hash == hash_value


@pytest.mark.parametrize("num_vuln_expected", [1, 0, 0])
@pytest.mark.parametrize("fixture_path,fixture_module", DDBBS)
def test_sql_injection_deduplication(fixture_path, fixture_module, num_vuln_expected, iast_span_deduplication_enabled):
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

    if num_vuln_expected == 0:
        assert span_report is None
    else:
        assert span_report

        assert len(span_report.vulnerabilities) == num_vuln_expected
