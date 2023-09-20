import copy
import json
import os

from mock.mock import ANY
import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast._utils import _is_python_version_supported as python_supported_by_iast
from ddtrace.appsec._iast.constants import VULN_CMDI
from ddtrace.appsec._iast.reporter import Evidence
from ddtrace.appsec._iast.reporter import IastSpanReporter
from ddtrace.appsec._iast.reporter import Location
from ddtrace.appsec._iast.reporter import Source
from ddtrace.appsec._iast.reporter import Vulnerability
from ddtrace.appsec._iast.taint_sinks.command_injection import CommandInjection
from ddtrace.internal import core


if python_supported_by_iast():
    from ddtrace.appsec._iast._taint_tracking import OriginType
    from ddtrace.appsec._iast._taint_tracking import Source as RangeSource
    from ddtrace.appsec._iast._taint_tracking import TaintRange
    from ddtrace.appsec._iast._taint_tracking import new_pyobject_id
    from ddtrace.appsec._iast._taint_tracking import set_ranges
    from ddtrace.appsec._iast._taint_tracking import str_to_origin


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


def _taint_pyobject_multiranges(pyobject, elements):
    pyobject_ranges = []

    len_pyobject = len(pyobject)
    pyobject_newid = new_pyobject_id(pyobject, len_pyobject)

    for element in elements:
        source_name, source_value, source_origin, start, len_range = element
        if isinstance(source_name, (bytes, bytearray)):
            source_name = str(source_name, encoding="utf8")
        if isinstance(source_value, (bytes, bytearray)):
            source_value = str(source_value, encoding="utf8")
        if source_origin is None:
            source_origin = OriginType.PARAMETER
        source = RangeSource(source_name, source_value, source_origin)
        pyobject_range = TaintRange(start, len_range, source)
        pyobject_ranges.append(pyobject_range)

    set_ranges(pyobject_newid, pyobject_ranges)
    return pyobject_newid


def get_parametrize(vuln_type):
    fixtures_filename = os.path.join(ROOT_DIR, "redaction_fixtures", "evidence-redaction-suite.json")
    data = json.loads(open(fixtures_filename).read())
    for element in data["suite"]:
        if element["type"] == "VULNERABILITIES":
            evidence_input = [ev["evidence"] for ev in element["input"] if ev["type"] == vuln_type]
            if evidence_input:
                sources_expected = element["expected"]["sources"][0]
                vulnerabilities_expected = element["expected"]["vulnerabilities"][0]
                parameters = element.get("parameters", [])
                if parameters:
                    for replace, values in parameters.items():
                        for value in values:
                            evidence_input_copy = copy.deepcopy(evidence_input[0])
                            vulnerabilities_expected_copy = copy.deepcopy(vulnerabilities_expected)
                            evidence_input_copy["value"] = evidence_input_copy["value"].replace(replace, value)
                            for value_part in vulnerabilities_expected_copy["evidence"]["valueParts"]:
                                if value_part.get("value"):
                                    value_part["value"] = value_part["value"].replace(replace, value)

                            yield evidence_input_copy, sources_expected, vulnerabilities_expected_copy
                else:
                    yield evidence_input[0], sources_expected, vulnerabilities_expected


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
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
    span_report.sources[0]

    assert vulnerability.type == VULN_CMDI
    assert vulnerability.evidence.valueParts == vulnerabilities_expected["evidence"]["valueParts"]


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
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


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
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


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
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
