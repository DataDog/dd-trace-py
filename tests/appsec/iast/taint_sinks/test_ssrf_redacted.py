import os

import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast._taint_tracking import str_to_origin
from ddtrace.appsec._iast.constants import VULN_SSRF
from ddtrace.appsec._iast.reporter import Evidence
from ddtrace.appsec._iast.reporter import IastSpanReporter
from ddtrace.appsec._iast.reporter import Location
from ddtrace.appsec._iast.reporter import Source
from ddtrace.appsec._iast.reporter import Vulnerability
from ddtrace.appsec._iast.taint_sinks.ssrf import SSRF
from ddtrace.internal import core
from tests.appsec.iast.taint_sinks.test_taint_sinks_utils import _taint_pyobject_multiranges
from tests.appsec.iast.taint_sinks.test_taint_sinks_utils import get_parametrize


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


@pytest.mark.parametrize(
    "evidence_input, sources_expected, vulnerabilities_expected", list(get_parametrize(VULN_SSRF))[0:2]
)
def test_ssrf_redaction_suite(evidence_input, sources_expected, vulnerabilities_expected, iast_span_defaults):
    # TODO: fix get_parametrize(VULN_SSRF)[2:] replacements doesn't work correctly with params of SSRF
    tainted_object = evidence_input_value = evidence_input.get("value", "")
    if evidence_input_value:
        tainted_object = _taint_pyobject_multiranges(
            evidence_input_value,
            [
                (
                    input_ranges["iinfo"]["parameterName"],
                    input_ranges["iinfo"]["parameterValue"],
                    str_to_origin(input_ranges["iinfo"]["type"]),
                    input_ranges["start"],
                    input_ranges["end"] - input_ranges["start"],
                )
                for input_ranges in evidence_input.get("ranges", {})
            ],
        )

    SSRF.report(tainted_object)

    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    assert span_report

    vulnerability = list(span_report.vulnerabilities)[0]

    assert vulnerability.type == VULN_SSRF
    assert vulnerability.evidence.valueParts == vulnerabilities_expected["evidence"]["valueParts"]


def test_cmdi_redact_param():
    ev = Evidence(
        valueParts=[
            {"value": "https://www.domain1.com/?id="},
            {"value": "test1234", "source": 0},
            {"value": "&param2=value2&param3=value3&param3=value3"},
        ]
    )
    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type="VulnerabilityType", evidence=ev, location=loc)
    s = Source(origin="http.request.parameter.name", name="password", value="test1234")
    report = IastSpanReporter([s], {v})

    redacted_report = SSRF._redact_report(report)
    for v in redacted_report.vulnerabilities:
        assert v.evidence.valueParts == [
            {"value": "https://www.domain1.com/?id="},
            {"pattern": "abcdefgh", "redacted": True, "source": 0},
            {"value": "&param2=value2&param3=value3&param3=value3"},
        ]


def test_cmdi_redact_user_password():
    ev = Evidence(
        valueParts=[
            {"value": "https://"},
            {"value": "root", "source": 0},
            {"value": ":"},
            {"value": "superpasswordsecure", "source": 1},
            {"value": "@domain1.com/?id="},
            {"value": "&param2=value2&param3=value3&param3=value3"},
        ]
    )
    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type="VulnerabilityType", evidence=ev, location=loc)
    s1 = Source(origin="http.request.parameter.name", name="username", value="root")
    s2 = Source(origin="http.request.parameter.name", name="password", value="superpasswordsecure")
    report = IastSpanReporter([s1, s2], {v})

    redacted_report = SSRF._redact_report(report)
    for v in redacted_report.vulnerabilities:
        assert v.evidence.valueParts == [
            {"value": "https://"},
            {"pattern": "abcd", "redacted": True, "source": 0},
            {"value": ":"},
            {"source": 1, "value": "superpasswordsecure"},
            {"value": "@domain1.com/?id="},
            {"value": "&param2=value2&param3=value3&param3=value3"},
        ]
