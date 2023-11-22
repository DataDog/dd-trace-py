from mock.mock import ANY
import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast._taint_tracking import str_to_origin
from ddtrace.appsec._iast.constants import VULN_CMDI
from ddtrace.appsec._iast.reporter import Evidence
from ddtrace.appsec._iast.reporter import IastSpanReporter
from ddtrace.appsec._iast.reporter import Location
from ddtrace.appsec._iast.reporter import Source
from ddtrace.appsec._iast.reporter import Vulnerability
from ddtrace.appsec._iast.taint_sinks.command_injection import CommandInjection
from ddtrace.internal import core
from tests.appsec.iast.taint_sinks.test_taint_sinks_utils import _taint_pyobject_multiranges
from tests.appsec.iast.taint_sinks.test_taint_sinks_utils import get_parametrize


@pytest.mark.parametrize("evidence_input, sources_expected, vulnerabilities_expected", list(get_parametrize(VULN_CMDI)))
def test_cmdi_redaction_suite(evidence_input, sources_expected, vulnerabilities_expected, iast_span_defaults):
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

    CommandInjection.report(tainted_object)

    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    assert span_report

    vulnerability = list(span_report.vulnerabilities)[0]

    assert vulnerability.type == VULN_CMDI
    assert vulnerability.evidence.valueParts == vulnerabilities_expected["evidence"]["valueParts"]


@pytest.mark.parametrize(
    "file_path",
    [
        "/mytest/folder/",
        "mytest/folder/",
        "mytest/folder",
        "../mytest/folder/",
        "../mytest/folder/",
        "../mytest/folder",
        "/mytest/folder/",
        "/mytest/folder/",
        "/mytest/folder",
        "/mytest/../folder/",
        "mytest/../folder/",
        "mytest/../folder",
        "../mytest/../folder/",
        "../mytest/../folder/",
        "../mytest/../folder",
        "/mytest/../folder/",
        "/mytest/../folder/",
        "/mytest/../folder",
        "/mytest/folder/file.txt",
        "mytest/folder/file.txt",
        "../mytest/folder/file.txt",
        "/mytest/folder/file.txt",
        "mytest/../folder/file.txt",
        "../mytest/../folder/file.txt",
        "/mytest/../folder/file.txt",
    ],
)
def test_cmdi_redact_rel_paths(file_path):
    ev = Evidence(
        valueParts=[
            {"value": "sudo "},
            {"value": "ls "},
            {"value": file_path, "source": 0},
        ]
    )
    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type="VulnerabilityType", evidence=ev, location=loc)
    s = Source(origin="file", name="SomeName", value=file_path)
    report = IastSpanReporter([s], {v})

    redacted_report = CommandInjection._redact_report(report)
    for v in redacted_report.vulnerabilities:
        assert v.evidence.valueParts == [
            {"value": "sudo "},
            {"value": "ls "},
            {"redacted": True, "pattern": ANY, "source": 0},
        ]


@pytest.mark.parametrize(
    "file_path",
    [
        "2 > /mytest/folder/",
        "2 > mytest/folder/",
        "-p mytest/folder",
        "--path=../mytest/folder/",
        "--path=../mytest/folder/",
        "--options ../mytest/folder",
        "-a /mytest/folder/",
        "-b /mytest/folder/",
        "-c /mytest/folder",
    ],
)
def test_cmdi_redact_options(file_path):
    ev = Evidence(
        valueParts=[
            {"value": "sudo "},
            {"value": "ls "},
            {"value": file_path, "source": 0},
        ]
    )
    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type="VulnerabilityType", evidence=ev, location=loc)
    s = Source(origin="file", name="SomeName", value=file_path)
    report = IastSpanReporter([s], {v})

    redacted_report = CommandInjection._redact_report(report)
    for v in redacted_report.vulnerabilities:
        assert v.evidence.valueParts == [
            {"value": "sudo "},
            {"value": "ls "},
            {"redacted": True, "pattern": ANY, "source": 0},
        ]


@pytest.mark.parametrize(
    "file_path",
    [
        " 2 > /mytest/folder/",
        " 2 > mytest/folder/",
        " -p mytest/folder",
        " --path=../mytest/folder/",
        " --path=../mytest/folder/",
        " --options ../mytest/folder",
        " -a /mytest/folder/",
        " -b /mytest/folder/",
        " -c /mytest/folder",
    ],
)
def test_cmdi_redact_source_command(file_path):
    ev = Evidence(
        valueParts=[
            {"value": "sudo "},
            {"value": "ls ", "source": 0},
            {"value": file_path},
        ]
    )
    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type="VulnerabilityType", evidence=ev, location=loc)
    s = Source(origin="SomeOrigin", name="SomeName", value="SomeValue")
    report = IastSpanReporter([s], {v})

    redacted_report = CommandInjection._redact_report(report)
    for v in redacted_report.vulnerabilities:
        assert v.evidence.valueParts == [
            {"value": "sudo "},
            {"value": "ls ", "source": 0},
            {"value": " "},
            {"redacted": True},
        ]
