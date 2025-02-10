import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import origin_to_str
from ddtrace.appsec._iast._taint_tracking import str_to_origin
from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect
from ddtrace.appsec._iast.constants import VULN_SQL_INJECTION
from ddtrace.appsec._iast.reporter import Evidence
from ddtrace.appsec._iast.reporter import IastSpanReporter
from ddtrace.appsec._iast.reporter import Location
from ddtrace.appsec._iast.reporter import Vulnerability
from ddtrace.appsec._iast.taint_sinks.sql_injection import SqlInjection
from tests.appsec.iast.taint_sinks._taint_sinks_utils import _taint_pyobject_multiranges
from tests.appsec.iast.taint_sinks._taint_sinks_utils import get_parametrize
from tests.appsec.iast.taint_sinks.conftest import _get_iast_data
from tests.utils import override_global_config


@pytest.mark.parametrize(
    "evidence_input,sources_expected,vulnerabilities_expected,element",
    list(get_parametrize(VULN_SQL_INJECTION)),
)
def test_sqli_redaction_suite(
    evidence_input, sources_expected, vulnerabilities_expected, iast_context_defaults, element
):
    with override_global_config(dict(_iast_deduplication_enabled=False)):
        tainted_object = _taint_pyobject_multiranges(
            evidence_input["value"],
            [
                (
                    input_ranges["iinfo"]["parameterName"],
                    input_ranges["iinfo"]["parameterValue"],
                    str_to_origin(input_ranges["iinfo"]["type"]),
                    input_ranges["start"],
                    input_ranges["end"] - input_ranges["start"],
                )
                for input_ranges in evidence_input["ranges"]
            ],
        )

        assert is_pyobject_tainted(tainted_object)

        SqlInjection.report(tainted_object)

        data = _get_iast_data()
        vulnerability = list(data["vulnerabilities"])[0]
        source = list(data["sources"])[0]
        source["origin"] = origin_to_str(source["origin"])

        assert vulnerability["type"] == VULN_SQL_INJECTION
        assert source == sources_expected


def test_redacted_report_no_match(iast_context_defaults):
    string_evicence = taint_pyobject(
        pyobject="SomeEvidenceValue", source_name="source_name", source_value="SomeEvidenceValue"
    )
    ev = Evidence(value=string_evicence)
    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type=VULN_SQL_INJECTION, evidence=ev, location=loc)
    report = IastSpanReporter(vulnerabilities={v})
    report.add_ranges_to_evidence_and_extract_sources(v)
    result = report.build_and_scrub_value_parts()

    assert result["vulnerabilities"]

    for v in result["vulnerabilities"]:
        assert v["evidence"] == {"valueParts": [{"source": 0, "value": "SomeEvidenceValue"}]}

    for v in result["sources"]:
        assert v == {"name": "source_name", "origin": OriginType.PARAMETER, "value": "SomeEvidenceValue"}


def test_redacted_report_source_name_match(iast_context_defaults):
    string_evicence = taint_pyobject(pyobject="'SomeEvidenceValue'", source_name="secret", source_value="SomeValue")
    ev = Evidence(value=string_evicence)
    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type=VULN_SQL_INJECTION, evidence=ev, location=loc)
    report = IastSpanReporter(vulnerabilities={v})
    report.add_ranges_to_evidence_and_extract_sources(v)
    result = report.build_and_scrub_value_parts()

    assert result["vulnerabilities"]

    for v in result["vulnerabilities"]:
        assert v["evidence"] == {"valueParts": [{"pattern": "*******************", "redacted": True, "source": 0}]}

    for v in result["sources"]:
        assert v == {"name": "secret", "origin": OriginType.PARAMETER, "pattern": "abcdefghi", "redacted": True}


def test_redacted_report_source_value_match(iast_context_defaults):
    string_evicence = taint_pyobject(
        pyobject="'SomeEvidenceValue'", source_name="SomeName", source_value="somepassword"
    )
    ev = Evidence(value=string_evicence)
    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type=VULN_SQL_INJECTION, evidence=ev, location=loc)
    report = IastSpanReporter(vulnerabilities={v})
    report.add_ranges_to_evidence_and_extract_sources(v)
    result = report.build_and_scrub_value_parts()

    assert result["vulnerabilities"]

    for v in result["vulnerabilities"]:
        assert v["evidence"] == {"valueParts": [{"pattern": "*******************", "redacted": True, "source": 0}]}

    for v in result["sources"]:
        assert v == {"name": "SomeName", "origin": OriginType.PARAMETER, "pattern": "abcdefghijkl", "redacted": True}


def test_redacted_report_evidence_value_match_also_redacts_source_value(iast_context_defaults):
    string_evicence = taint_pyobject(
        pyobject="'SomeSecretPassword'", source_name="SomeName", source_value="SomeSecretPassword"
    )
    ev = Evidence(value=string_evicence)
    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type=VULN_SQL_INJECTION, evidence=ev, location=loc)
    report = IastSpanReporter(vulnerabilities={v})
    report.add_ranges_to_evidence_and_extract_sources(v)
    result = report.build_and_scrub_value_parts()

    assert result["vulnerabilities"]

    for v in result["vulnerabilities"]:
        assert v["evidence"] == {"valueParts": [{"pattern": "********************", "redacted": True, "source": 0}]}

    for v in result["sources"]:
        assert v == {
            "name": "SomeName",
            "origin": OriginType.PARAMETER,
            "pattern": "abcdefghijklmnopqr",
            "redacted": True,
        }


def test_redacted_report_valueparts(iast_context_defaults):
    string_evicence = taint_pyobject(pyobject="1234", source_name="SomeName", source_value="SomeValue")

    ev = Evidence(value=add_aspect("SELECT * FROM users WHERE password = '", add_aspect(string_evicence, ":{SHA1}'")))
    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type=VULN_SQL_INJECTION, evidence=ev, location=loc)
    report = IastSpanReporter(vulnerabilities={v})
    report.add_ranges_to_evidence_and_extract_sources(v)
    result = report.build_and_scrub_value_parts()

    assert result["vulnerabilities"]

    for v in result["vulnerabilities"]:
        assert v["evidence"] == {
            "valueParts": [
                {"value": "SELECT * FROM users WHERE password = '"},
                {"pattern": "****", "redacted": True, "source": 0},
                {"redacted": True},
                {"value": "'"},
            ]
        }

    for v in result["sources"]:
        assert v == {"name": "SomeName", "origin": OriginType.PARAMETER, "pattern": "abcdefghi", "redacted": True}


def test_redacted_report_valueparts_username_not_tainted(iast_context_defaults):
    string_evicence = taint_pyobject(pyobject="secret", source_name="SomeName", source_value="SomeValue")

    string_tainted = add_aspect(
        "SELECT * FROM users WHERE username = '",
        add_aspect("pepito", add_aspect("' AND password = '", add_aspect(string_evicence, "'"))),
    )
    ev = Evidence(value=string_tainted, dialect="POSTGRES")
    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type=VULN_SQL_INJECTION, evidence=ev, location=loc)
    report = IastSpanReporter(vulnerabilities={v})
    report.add_ranges_to_evidence_and_extract_sources(v)
    result = report.build_and_scrub_value_parts()

    assert result["vulnerabilities"]

    for v in result["vulnerabilities"]:
        assert v["evidence"] == {
            "valueParts": [
                {"value": "SELECT * FROM users WHERE username = '"},
                {"redacted": True},
                {"value": "' AND password = '"},
                {"pattern": "******", "redacted": True, "source": 0},
                {"value": "'"},
            ]
        }

    for v in result["sources"]:
        assert v == {"name": "SomeName", "origin": OriginType.PARAMETER, "pattern": "abcdefghi", "redacted": True}


def test_redacted_report_valueparts_username_tainted(iast_context_defaults):
    string_evicence = taint_pyobject(pyobject="secret", source_name="SomeName", source_value="SomeValue")

    string_tainted = add_aspect(
        "SELECT * FROM users WHERE username = '",
        add_aspect(string_evicence, add_aspect("' AND password = '", add_aspect(string_evicence, "'"))),
    )
    ev = Evidence(value=string_tainted, dialect="POSTGRES")
    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type=VULN_SQL_INJECTION, evidence=ev, location=loc)
    report = IastSpanReporter(vulnerabilities={v})
    report.add_ranges_to_evidence_and_extract_sources(v)
    result = report.build_and_scrub_value_parts()

    assert result["vulnerabilities"]

    for v in result["vulnerabilities"]:
        assert v["evidence"] == {
            "valueParts": [
                {"value": "SELECT * FROM users WHERE username = '"},
                {"pattern": "******", "redacted": True, "source": 0},
                {"value": "' AND password = '"},
                {"pattern": "******", "redacted": True, "source": 0},
                {"value": "'"},
            ]
        }

    for v in result["sources"]:
        assert v == {"name": "SomeName", "origin": OriginType.PARAMETER, "pattern": "abcdefghi", "redacted": True}


def test_regression_ci_failure(iast_context_defaults):
    string_evicence = taint_pyobject(pyobject="master", source_name="SomeName", source_value="master")

    string_tainted = add_aspect(
        "SELECT tbl_name FROM sqlite_", add_aspect(string_evicence, "WHERE tbl_name LIKE 'password'")
    )
    ev = Evidence(value=string_tainted, dialect="POSTGRES")
    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type=VULN_SQL_INJECTION, evidence=ev, location=loc)
    report = IastSpanReporter(vulnerabilities={v})
    report.add_ranges_to_evidence_and_extract_sources(v)
    result = report.build_and_scrub_value_parts()

    assert result["vulnerabilities"]

    for v in result["vulnerabilities"]:
        assert v["evidence"] == {
            "valueParts": [
                {"value": "SELECT tbl_name FROM sqlite_"},
                {"source": 0, "value": "master"},
                {"value": "WHERE tbl_name LIKE '"},
                {"redacted": True},
                {"value": "'"},
            ]
        }

    for v in result["sources"]:
        assert v == {"name": "SomeName", "origin": OriginType.PARAMETER, "value": "master"}
