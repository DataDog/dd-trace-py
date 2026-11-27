import time

import pytest

from ddtrace.appsec._iast._evidence_redaction._sensitive_handler import SensitiveHandler
from ddtrace.appsec._iast._evidence_redaction.sql_sensitive_analyzer import sql_sensitive_analyzer
from ddtrace.appsec._iast._report_types import Evidence
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import origin_to_str
from ddtrace.appsec._iast._taint_tracking import str_to_origin
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect
from ddtrace.appsec._iast.constants import DBAPI_PSYCOPG
from ddtrace.appsec._iast.constants import DBAPI_SQLITE
from ddtrace.appsec._iast.constants import VULN_SQL_INJECTION
from ddtrace.appsec._iast.reporter import IastSpanReporter
from ddtrace.appsec._iast.reporter import Location
from ddtrace.appsec._iast.reporter import Vulnerability
from ddtrace.appsec._iast.taint_sinks.sql_injection import SqlInjection
from tests.appsec.iast.iast_utils import _get_iast_data
from tests.appsec.iast.taint_sinks._taint_sinks_utils import _taint_pyobject_multiranges
from tests.appsec.iast.taint_sinks._taint_sinks_utils import get_parametrize
from tests.utils import override_global_config


@pytest.mark.parametrize(
    "evidence_input,sources_expected,vulnerabilities_expected,element",
    list(get_parametrize(VULN_SQL_INJECTION)),
)
def test_sqli_redaction_suite(
    evidence_input, sources_expected, vulnerabilities_expected, iast_context_defaults, element
):
    with override_global_config(dict(_iast_deduplication_enabled=False, _iast_max_vulnerabilities_per_requests=1000)):
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

        assert SqlInjection.report(tainted_object)

        data = _get_iast_data()
        vulnerability = list(data["vulnerabilities"])[0]
        source = list(data["sources"])[0]
        source["origin"] = origin_to_str(source["origin"])

        assert vulnerability["type"] == VULN_SQL_INJECTION
        assert source == sources_expected


@pytest.mark.parametrize("dialect", [DBAPI_SQLITE, DBAPI_PSYCOPG])
@pytest.mark.parametrize(
    "literal", ["12.345E3", "12.345E+3", "12.345E-3", "12E-3", "1e10", "1.e2", "-.5e+2", "+1.E-2", "1e2F", "1e2d"]
)
def test_sql_sensitive_analyzer_redacts_whole_exponent_literal(dialect, literal):
    prefix = "SELECT "
    evidence = Evidence(value=prefix + literal + " AS result", dialect=dialect)

    assert sql_sensitive_analyzer(evidence, None, None) == [{"start": len(prefix), "end": len(prefix) + len(literal)}]


@pytest.mark.parametrize("dialect", [DBAPI_SQLITE, DBAPI_PSYCOPG])
@pytest.mark.parametrize("suffix", ["E", "e+", "E-", "e10foo"])
def test_sql_sensitive_analyzer_does_not_extend_redaction_past_mantissa(dialect, suffix):
    prefix = "SELECT E10, name1e10, item_1E2, "
    evidence = Evidence(value=prefix + "1" + suffix, dialect=dialect)

    assert sql_sensitive_analyzer(evidence, None, None) == [{"start": len(prefix), "end": len(prefix) + 1}]


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


@pytest.mark.parametrize("password_first", [True, False])
def test_redacted_report_only_clears_redacted_source_values(iast_context_defaults, password_first):
    password = taint_pyobject(
        pyobject="hunter", source_name="password", source_value="hunter", source_origin=OriginType.PARAMETER
    )
    order_by = taint_pyobject(
        pyobject="id", source_name="order_by", source_value="id", source_origin=OriginType.PARAMETER
    )

    if password_first:
        string_tainted = add_aspect(
            add_aspect(add_aspect("SELECT * FROM users WHERE password = '", password), "' ORDER BY "), order_by
        )
    else:
        string_tainted = add_aspect(
            add_aspect(add_aspect(add_aspect("SELECT ", order_by), " FROM users WHERE password = '"), password), "'"
        )
    ev = Evidence(value=string_tainted, dialect="POSTGRES")
    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type=VULN_SQL_INJECTION, evidence=ev, location=loc)
    report = IastSpanReporter(vulnerabilities={v})
    report.add_ranges_to_evidence_and_extract_sources(v)
    result = report.build_and_scrub_value_parts()

    sources = {source["name"]: source for source in result["sources"]}
    assert sources.keys() == {"password", "order_by"}
    assert sources["password"]["redacted"] is True
    assert "value" not in sources["password"]
    assert sources["order_by"] == {"name": "order_by", "origin": OriginType.PARAMETER, "value": "id"}


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


@pytest.mark.parametrize(
    "query,expected_redacted",
    [
        ("SELECT $$secret$$", ["secret"]),
        ("SELECT $tag$secret$tag$", ["secret"]),
        ("SELECT $$line1\nsecret$$", ["line1\nsecret"]),
        ("SELECT $$$$", [""]),
        ("SELECT $t$one$T$sample$t$", ["one$T$sample"]),
        ("SELECT $é$secret$é$", ["secret"]),
        ("SELECT $€$secret$€$", ["secret"]),
        ("SELECT $e\u0301$secret$e\u0301$", ["secret"]),
        ("SELECT $$a$$$$b$$", ["a", "b"]),
        ("SELECT $a$x$a$b$y$b$", ["x"]),
        ("SELECT col$t$, 'label $t$sample' FROM tab", ["label $t$sample"]),
        ("SELECT $unclosed$ $b$secret$b$", ["secret"]),
        ("SELECT $1 + $2 + $1 + $2", ["1", "2", "1", "2"]),
        (
            "SELECT * FROM users WHERE username = $escape$I'm an escaped string$escape$ "
            "AND last_name = $TEST$another$$$TEST$",
            ["I'm an escaped string", "another$$"],
        ),
    ],
)
def test_psycopg_dollar_quoted_literals_redacted(query, expected_redacted):
    handler = SensitiveHandler()
    ranges = sql_sensitive_analyzer(
        Evidence(value=query, dialect=DBAPI_PSYCOPG), handler._name_pattern, handler._value_pattern
    )
    assert [query[r["start"] : r["end"]] for r in ranges] == expected_redacted


def test_psycopg_unclosed_dollar_tags_linear_time():
    handler = SensitiveHandler()
    query = " ".join(f"$t{i}$" for i in range(5000))
    start = time.perf_counter()
    ranges = sql_sensitive_analyzer(
        Evidence(value=query, dialect=DBAPI_PSYCOPG), handler._name_pattern, handler._value_pattern
    )
    assert ranges == []
    assert time.perf_counter() - start < 0.5
