import os

import pytest

from ddtrace.appsec.iast._utils import _is_python_version_supported as python_supported_by_iast
from ddtrace.appsec.iast.reporter import Evidence
from ddtrace.appsec.iast.reporter import IastSpanReporter
from ddtrace.appsec.iast.reporter import Location
from ddtrace.appsec.iast.reporter import Source
from ddtrace.appsec.iast.reporter import Vulnerability


if python_supported_by_iast():
    from ddtrace.appsec.iast.taint_sinks.command_injection import CommandInjection

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


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
    s = Source(origin="SomeOrigin", name="SomeName", value="SomeValue")
    report = IastSpanReporter([s], {v})

    redacted_report = CommandInjection._redact_report(report)
    for v in redacted_report.vulnerabilities:
        assert v.evidence.valueParts == [{"value": "sudo "}, {"value": "ls "}, {"redacted": True, "source": 0}]


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
    s = Source(origin="SomeOrigin", name="SomeName", value="SomeValue")
    report = IastSpanReporter([s], {v})

    redacted_report = CommandInjection._redact_report(report)
    for v in redacted_report.vulnerabilities:
        assert v.evidence.valueParts == [{"value": "sudo "}, {"value": "ls "}, {"redacted": True, "source": 0}]


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
        "-a /mytest/folder/",
        "-b /mytest/folder/",
        "-c /mytest/folder",
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
        assert v.evidence.valueParts == [{"value": "sudo "}, {"value": "ls ", "source": 0}, {"redacted": True}]
