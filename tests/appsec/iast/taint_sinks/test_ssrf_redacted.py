import os

import pytest

from ddtrace.appsec._iast._taint_tracking import origin_to_str
from ddtrace.appsec._iast._taint_tracking import str_to_origin
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect
from ddtrace.appsec._iast.constants import VULN_SSRF
from ddtrace.appsec._iast.reporter import Evidence
from ddtrace.appsec._iast.reporter import IastSpanReporter
from ddtrace.appsec._iast.reporter import Location
from ddtrace.appsec._iast.reporter import Vulnerability
from ddtrace.appsec._iast.taint_sinks.ssrf import SSRF
from tests.appsec.iast.taint_sinks._taint_sinks_utils import _taint_pyobject_multiranges
from tests.appsec.iast.taint_sinks._taint_sinks_utils import get_parametrize
from tests.appsec.iast.taint_sinks.conftest import _get_iast_data


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


@pytest.mark.parametrize(
    "evidence_input,sources_expected,vulnerabilities_expected,element", list(get_parametrize(VULN_SSRF))
)
def test_ssrf_redaction_suite(
    evidence_input, sources_expected, vulnerabilities_expected, iast_context_defaults, element
):
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

    data = _get_iast_data()
    vulnerability = list(data["vulnerabilities"])[0]
    source = list(data["sources"])[0]
    source["origin"] = origin_to_str(source["origin"])

    assert vulnerability["type"] == VULN_SSRF
    assert vulnerability["evidence"] == vulnerabilities_expected["evidence"]
    assert source == sources_expected


def test_ssrf_redact_param(iast_context_defaults):
    password_taint_range = taint_pyobject(pyobject="test1234", source_name="password", source_value="test1234")

    ev = Evidence(
        value=add_aspect(
            "https://www.domain1.com/?id=",
            add_aspect(password_taint_range, "&param2=value2&param3=value3&param3=value3"),
        )
    )

    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type=VULN_SSRF, evidence=ev, location=loc)
    report = IastSpanReporter(vulnerabilities={v})
    report.add_ranges_to_evidence_and_extract_sources(v)
    result = report.build_and_scrub_value_parts()

    assert result["vulnerabilities"]
    for v in result["vulnerabilities"]:
        assert v["evidence"]["valueParts"] == [
            {"value": "https://www.domain1.com/?id="},
            {"pattern": "abcdefgh", "redacted": True, "source": 0},
            {"value": "&param2="},
            {"redacted": True},
            {"value": "&param3="},
            {"redacted": True},
            {"value": "&param3="},
            {"redacted": True},
        ]


def test_ssrf_redact_params_log_url(iast_context_defaults):
    url = (
        "http://dovahkiin.dovahkiin.naal.ok/zin/los/vahriin/wahdein-vokul-mahfaeraak.ast-vaal-4ee5-aa01-3de356fd2cd8/snapshots/"
        "f7591b64-9839-4be8-a3a9-e7ccf5c0e17e?schemaVersion=c27f6149-36b6-4f3a-bacb-ba4b8db36892"
    )
    tainted_source = taint_pyobject(pyobject=url, source_name="encoded_forward_api", source_value=url)

    ev = Evidence(value=tainted_source)

    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type=VULN_SSRF, evidence=ev, location=loc)
    report = IastSpanReporter(vulnerabilities={v})
    report.add_ranges_to_evidence_and_extract_sources(v)
    result = report.build_and_scrub_value_parts()

    assert result["vulnerabilities"]
    for v in result["vulnerabilities"]:
        assert v["evidence"]["valueParts"] == [
            {
                "source": 0,
                "value": "http://dovahkiin.dovahkiin.naal.ok/zin/los/vahriin/wahdein-vokul-mahfaeraak.ast-vaal-4ee5-aa01-3de356fd"
                "2cd8/snapshots/f7591b64-9839-4be8-a3a9-e7ccf5c0e17e?schemaVersion=",
            },
            {"pattern": "TUVWXYZ0123456789abcdefghijklmnopqrs", "redacted": True, "source": 0},
        ]


def test_cmdi_redact_user_password(iast_context_defaults):
    user_taint_range = taint_pyobject(pyobject="root", source_name="username", source_value="root")
    password_taint_range = taint_pyobject(
        pyobject="superpasswordsecure", source_name="password", source_value="superpasswordsecure"
    )

    ev = Evidence(
        value=add_aspect(
            "https://",
            add_aspect(
                add_aspect(add_aspect(user_taint_range, ":"), password_taint_range),
                "@domain1.com/?id=&param2=value2&param3=value3&param3=value3",
            ),
        )
    )

    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type=VULN_SSRF, evidence=ev, location=loc)
    report = IastSpanReporter(vulnerabilities={v})
    report.add_ranges_to_evidence_and_extract_sources(v)
    result = report.build_and_scrub_value_parts()

    assert result["vulnerabilities"]
    for v in result["vulnerabilities"]:
        assert v["evidence"]["valueParts"] == [
            {"value": "https://"},
            {"pattern": "abcd", "redacted": True, "source": 0},
            {"redacted": True},
            {"pattern": "abcdefghijklmnopqrs", "redacted": True, "source": 1},
            {"value": "@domain1.com/?id=&param2="},
            {"redacted": True},
            {"value": "&param3="},
            {"redacted": True},
            {"value": "&param3="},
            {"redacted": True},
        ]
