from mock.mock import ANY
import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast._taint_tracking import origin_to_str
from ddtrace.appsec._iast._taint_tracking import str_to_origin
from ddtrace.appsec._iast._taint_tracking import taint_pyobject
from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect
from ddtrace.appsec._iast.constants import VULN_CMDI
from ddtrace.appsec._iast.reporter import Evidence
from ddtrace.appsec._iast.reporter import IastSpanReporter
from ddtrace.appsec._iast.reporter import Location
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

    span_report.build_and_scrub_value_parts()
    result = span_report._to_dict()
    vulnerability = list(result["vulnerabilities"])[0]
    source = list(result["sources"])[0]
    source["origin"] = origin_to_str(source["origin"])

    assert vulnerability["type"] == VULN_CMDI
    assert source == sources_expected


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
def test_cmdi_redact_rel_paths_and_sudo(file_path):
    file_path = taint_pyobject(pyobject=file_path, source_name="test_ossystem", source_value=file_path)
    ev = Evidence(value=add_aspect("sudo ", add_aspect("ls ", file_path)))
    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type=VULN_CMDI, evidence=ev, location=loc)
    report = IastSpanReporter(vulnerabilities={v})
    report.add_ranges_to_evidence_and_extract_sources(v)
    result = report.build_and_scrub_value_parts()

    assert result["vulnerabilities"]

    for v in result["vulnerabilities"]:
        assert v["evidence"]["valueParts"] == [
            {"value": "sudo ls "},
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
def test_cmdi_redact_sudo_command_with_options(file_path):
    file_path = taint_pyobject(pyobject=file_path, source_name="test_ossystem", source_value=file_path)
    ev = Evidence(value=add_aspect("sudo ", add_aspect("ls ", file_path)))
    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type=VULN_CMDI, evidence=ev, location=loc)
    report = IastSpanReporter(vulnerabilities={v})
    report.add_ranges_to_evidence_and_extract_sources(v)
    result = report.build_and_scrub_value_parts()

    assert result["vulnerabilities"]

    for v in result["vulnerabilities"]:
        assert v["evidence"]["valueParts"] == [
            {"value": "sudo ls "},
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
def test_cmdi_redact_command_with_options(file_path):
    file_path = taint_pyobject(pyobject=file_path, source_name="test_ossystem", source_value=file_path)
    ev = Evidence(value=add_aspect("ls ", file_path))
    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type=VULN_CMDI, evidence=ev, location=loc)
    report = IastSpanReporter(vulnerabilities={v})
    report.add_ranges_to_evidence_and_extract_sources(v)
    result = report.build_and_scrub_value_parts()

    assert result["vulnerabilities"]

    for v in result["vulnerabilities"]:
        assert v["evidence"]["valueParts"] == [
            {"value": "ls "},
            {"redacted": True, "pattern": ANY, "source": 0},
        ]


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
    file_path = taint_pyobject(pyobject=file_path, source_name="test_ossystem", source_value=file_path)
    ev = Evidence(value=add_aspect("dir -l ", file_path))
    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type=VULN_CMDI, evidence=ev, location=loc)
    report = IastSpanReporter(vulnerabilities={v})
    report.add_ranges_to_evidence_and_extract_sources(v)
    result = report.build_and_scrub_value_parts()

    assert result["vulnerabilities"]

    for v in result["vulnerabilities"]:
        assert v["evidence"]["valueParts"] == [
            {"value": "dir "},
            {"redacted": True},
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
    Ls_cmd = taint_pyobject(pyobject="ls ", source_name="test_ossystem", source_value="ls ")

    ev = Evidence(value=add_aspect("sudo ", add_aspect(Ls_cmd, file_path)))
    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type=VULN_CMDI, evidence=ev, location=loc)
    report = IastSpanReporter(vulnerabilities={v})
    report.add_ranges_to_evidence_and_extract_sources(v)
    result = report.build_and_scrub_value_parts()

    assert result["vulnerabilities"]
    for v in result["vulnerabilities"]:
        assert v["evidence"]["valueParts"] == [
            {"value": "sudo "},
            {"value": "ls ", "source": 0},
            {"redacted": True},
        ]
