import os
from unittest.mock import ANY

import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import origin_to_str
from ddtrace.appsec._iast._taint_tracking import str_to_origin
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted
from ddtrace.appsec._iast.constants import VULN_PATH_TRAVERSAL
from ddtrace.appsec._iast.reporter import Evidence
from ddtrace.appsec._iast.reporter import IastSpanReporter
from ddtrace.appsec._iast.reporter import Location
from ddtrace.appsec._iast.reporter import Vulnerability
from ddtrace.appsec._iast.taint_sinks.path_traversal import PathTraversal
from tests.appsec.iast.taint_sinks._taint_sinks_utils import _taint_pyobject_multiranges
from tests.appsec.iast.taint_sinks._taint_sinks_utils import get_parametrize
from tests.appsec.iast.taint_sinks.conftest import _get_iast_data


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


@pytest.mark.parametrize(
    "file_path",
    [
        "1",
        "12",
        "123",
        "a",
        "ab",
        "AbC",
        "-",
        "txt",
        ".txt",
    ],
)
def test_path_traversal_redact_exclude(file_path, iast_context_defaults):
    file_path = taint_pyobject(pyobject=file_path, source_name="path_traversal", source_value=file_path)
    ev = Evidence(value=file_path)
    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type=VULN_PATH_TRAVERSAL, evidence=ev, location=loc)
    report = IastSpanReporter(vulnerabilities={v})
    report.add_ranges_to_evidence_and_extract_sources(v)
    result = report.build_and_scrub_value_parts()

    assert result == {
        "sources": [{"name": "path_traversal", "origin": OriginType.PARAMETER, "value": file_path}],
        "vulnerabilities": [
            {
                "evidence": {"valueParts": [{"source": 0, "value": file_path}]},
                "hash": ANY,
                "location": {"line": ANY, "path": "foobar.py", "spanId": ANY},
                "type": VULN_PATH_TRAVERSAL,
            }
        ],
    }


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
def test_path_traversal_redact_rel_paths(file_path, iast_context_defaults):
    file_path = taint_pyobject(pyobject=file_path, source_name="path_traversal", source_value=file_path)
    ev = Evidence(value=file_path)
    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type=VULN_PATH_TRAVERSAL, evidence=ev, location=loc)
    report = IastSpanReporter(vulnerabilities={v})
    report.add_ranges_to_evidence_and_extract_sources(v)
    result = report.build_and_scrub_value_parts()

    assert result == {
        "sources": [{"name": "path_traversal", "origin": OriginType.PARAMETER, "value": file_path}],
        "vulnerabilities": [
            {
                "evidence": {"valueParts": [{"source": 0, "value": file_path}]},
                "hash": ANY,
                "location": {"line": ANY, "path": "foobar.py", "spanId": ANY},
                "type": VULN_PATH_TRAVERSAL,
            }
        ],
    }


def test_path_traversal_redact_abs_paths(iast_context_defaults):
    file_path = os.path.join(ROOT_DIR, "../fixtures", "taint_sinks", "path_traversal_test_file.txt")
    file_path = taint_pyobject(pyobject=file_path, source_name="path_traversal", source_value=file_path)
    ev = Evidence(value=file_path)
    loc = Location(path="foobar.py", line=35, spanId=123)
    v = Vulnerability(type=VULN_PATH_TRAVERSAL, evidence=ev, location=loc)
    report = IastSpanReporter(vulnerabilities={v})
    report.add_ranges_to_evidence_and_extract_sources(v)
    result = report.build_and_scrub_value_parts()

    assert result == {
        "sources": [{"name": "path_traversal", "origin": OriginType.PARAMETER, "value": file_path}],
        "vulnerabilities": [
            {
                "evidence": {"valueParts": [{"source": 0, "value": file_path}]},
                "hash": ANY,
                "location": {"line": ANY, "path": "foobar.py", "spanId": ANY},
                "type": VULN_PATH_TRAVERSAL,
            }
        ],
    }


@pytest.mark.parametrize(
    "evidence_input,sources_expected,vulnerabilities_expected,element",
    list(get_parametrize(VULN_PATH_TRAVERSAL)),
)
def test_path_traversal_redaction_suite(
    evidence_input, sources_expected, vulnerabilities_expected, iast_context_defaults, element
):
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

    PathTraversal.report(tainted_object)

    data = _get_iast_data()

    vulnerability = list(data["vulnerabilities"])[0]
    source = list(data["sources"])[0]
    source["origin"] = origin_to_str(source["origin"])

    assert vulnerability["type"] == VULN_PATH_TRAVERSAL
    assert vulnerability["evidence"] == vulnerabilities_expected["evidence"]
    assert source == sources_expected
