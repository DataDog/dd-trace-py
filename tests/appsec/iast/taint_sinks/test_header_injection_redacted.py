import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking import str_to_origin
from ddtrace.appsec._iast.constants import VULN_HEADER_INJECTION
from ddtrace.appsec._iast.reporter import Evidence
from ddtrace.appsec._iast.reporter import IastSpanReporter
from ddtrace.appsec._iast.reporter import Location
from ddtrace.appsec._iast.reporter import Source
from ddtrace.appsec._iast.reporter import Vulnerability
from ddtrace.appsec._iast.taint_sinks.header_injection import HeaderInjection
from ddtrace.internal import core
from tests.appsec.iast.taint_sinks.test_taint_sinks_utils import _taint_pyobject_multiranges
from tests.appsec.iast.taint_sinks.test_taint_sinks_utils import get_parametrize
from tests.utils import override_global_config


@pytest.mark.parametrize(
    "header_name, header_value",
    [
        ("test", "aaaaaaaaaaaaaa"),
        ("test2", "9944b09199c62bcf9418ad846dd0e4bbdfc6ee4b"),
    ],
)
def test_header_injection_redact_excluded(header_name, header_value):
    ev = Evidence(
        valueParts=[
            {"value": header_name + ": "},
            {"value": header_value, "source": 0},
        ]
    )
    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type=VULN_HEADER_INJECTION, evidence=ev, location=loc)
    s = Source(origin="SomeOrigin", name="SomeName", value=header_value)
    report = IastSpanReporter([s], {v})

    redacted_report = HeaderInjection._redact_report(report)
    for v in redacted_report.vulnerabilities:
        assert v.evidence.valueParts == [{"value": header_name + ": "}, {"source": 0, "value": header_value}]


@pytest.mark.parametrize(
    "header_name, header_value, value_part",
    [
        (
            "WWW-Authenticate",
            'Basic realm="api"',
            [
                {"value": "WWW-Authenticate: "},
                {"pattern": "abcdefghijklmnopq", "redacted": True, "source": 0},
            ],
        ),
        (
            "Authorization",
            "Token 9944b09199c62bcf9418ad846dd0e4bbdfc6ee4b",
            [
                {"value": "Authorization: "},
                {
                    "pattern": "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRST",
                    "redacted": True,
                    "source": 0,
                },
            ],
        ),
    ],
)
def test_header_injection_redact(header_name, header_value, value_part):
    ev = Evidence(
        valueParts=[
            {"value": header_name + ": "},
            {"value": header_value, "source": 0},
        ]
    )
    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type=VULN_HEADER_INJECTION, evidence=ev, location=loc)
    s = Source(origin="SomeOrigin", name="SomeName", value=header_value)
    report = IastSpanReporter([s], {v})

    redacted_report = HeaderInjection._redact_report(report)
    for v in redacted_report.vulnerabilities:
        assert v.evidence.valueParts == value_part


@pytest.mark.skip(reason="TODO: this algorithm is not working as expected, it needs to be fixed.")
@pytest.mark.parametrize(
    "evidence_input, sources_expected, vulnerabilities_expected",
    list(get_parametrize(VULN_HEADER_INJECTION)),
)
def test_header_injection_redaction_suite(
    evidence_input, sources_expected, vulnerabilities_expected, iast_span_defaults
):
    with override_global_config(dict(_deduplication_enabled=False)):
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

        HeaderInjection.report(tainted_object)

        span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
        assert span_report

        vulnerability = list(span_report.vulnerabilities)[0]

        assert vulnerability.type == VULN_HEADER_INJECTION
        assert vulnerability.evidence.valueParts == vulnerabilities_expected["evidence"]["valueParts"]
