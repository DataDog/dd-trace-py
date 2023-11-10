import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast._utils import _is_python_version_supported as python_supported_by_iast
from ddtrace.appsec._iast.constants import VULN_SQL_INJECTION
from ddtrace.internal import core
from tests.appsec.iast.aspects.conftest import _iast_patched_module
from tests.appsec.iast.iast_utils import get_line_and_hash


try:
    from ddtrace.appsec._iast._taint_tracking import OriginType
    from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted
    from ddtrace.appsec._iast._taint_tracking import taint_pyobject
except (ImportError, AttributeError):
    pytest.skip("IAST not supported for this Python version", allow_module_level=True)


FIXTURES_PATH = "tests/appsec/iast/fixtures/taint_sinks/sql_injection.py"


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_sql_injection(iast_span_defaults):
    mod = _iast_patched_module("tests.appsec.iast.fixtures.taint_sinks.sql_injection")
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

    line, hash_value = get_line_and_hash("test_sql_injection", VULN_SQL_INJECTION, filename=FIXTURES_PATH)
    assert vulnerability.location.line == line
    assert vulnerability.location.path == FIXTURES_PATH
    assert vulnerability.hash == hash_value
