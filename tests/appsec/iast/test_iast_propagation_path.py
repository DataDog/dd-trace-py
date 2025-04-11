from unittest.mock import ANY

import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast.constants import VULN_PATH_TRAVERSAL
from tests.appsec.iast.iast_utils import _iast_patched_module
from tests.appsec.iast.iast_utils import get_line_and_hash
from tests.appsec.iast.taint_sinks.conftest import _get_span_report


FIXTURES_PATH = "tests/appsec/iast/fixtures/propagation_path.py"


def _assert_vulnerability(data, value_parts, file_line_label):
    vulnerability = data["vulnerabilities"][0]
    assert vulnerability["type"] == VULN_PATH_TRAVERSAL
    assert vulnerability["evidence"]["valueParts"] == value_parts
    assert "value" not in vulnerability["evidence"].keys()
    assert "pattern" not in vulnerability["evidence"].keys()
    assert "redacted" not in vulnerability["evidence"].keys()

    line, hash_value = get_line_and_hash(file_line_label, VULN_PATH_TRAVERSAL, filename=FIXTURES_PATH)
    assert vulnerability["location"]["path"] == FIXTURES_PATH
    assert vulnerability["location"]["line"] == line
    assert vulnerability["location"]["method"] == file_line_label
    assert vulnerability["location"]["class_name"] == ""
    assert vulnerability["hash"] == hash_value


def test_propagation_no_path(iast_context_defaults):
    mod = _iast_patched_module("tests.appsec.iast.fixtures.propagation_path")
    origin1 = "taintsource"
    tainted_string = taint_pyobject(origin1, source_name="path", source_value=origin1, source_origin=OriginType.PATH)
    for i in range(100):
        mod.propagation_no_path(tainted_string)

    span_report = _get_span_report()

    assert span_report is None


@pytest.mark.parametrize(
    "origin1",
    [
        ("taintsource"),
        ("1"),
        (b"taintsource1"),
        (bytearray(b"taintsource1")),
    ],
)
def test_propagation_path_1_origin_1_propagation(origin1, iast_context_defaults):
    mod = _iast_patched_module("tests.appsec.iast.fixtures.propagation_path")

    tainted_string = taint_pyobject(origin1, source_name="path", source_value=origin1, source_origin=OriginType.PATH)
    mod.propagation_path_1_source_1_prop(tainted_string)

    span_report = _get_span_report()
    span_report.build_and_scrub_value_parts()
    data = span_report._to_dict()
    sources = data["sources"]
    source_value_encoded = str(origin1, encoding="utf-8") if type(origin1) is not str else origin1

    assert len(sources) == 1
    assert sources[0]["name"] == "path"
    assert sources[0]["origin"] == OriginType.PATH
    assert sources[0]["value"] == source_value_encoded

    value_parts = [
        {"value": ANY},
        {"source": 0, "value": source_value_encoded},
        {"value": ".txt"},
    ]
    _assert_vulnerability(data, value_parts, "propagation_path_1_source_1_prop")


@pytest.mark.parametrize(
    "origin1",
    [
        "taintsource",
        "1",
        b"taintsource1",
        bytearray(b"taintsource1"),
    ],
)
def test_propagation_path_1_origins_2_propagations(origin1, iast_context_defaults):
    mod = _iast_patched_module("tests.appsec.iast.fixtures.propagation_path")

    tainted_string_1 = taint_pyobject(origin1, source_name="path1", source_value=origin1, source_origin=OriginType.PATH)

    mod.propagation_path_1_source_2_prop(tainted_string_1)

    span_report = _get_span_report()
    span_report.build_and_scrub_value_parts()
    data = span_report._to_dict()
    sources = data["sources"]
    value_encoded = str(origin1, encoding="utf-8") if type(origin1) is not str else origin1

    assert len(sources) == 1
    assert sources[0]["name"] == "path1"
    assert sources[0]["origin"] == OriginType.PATH
    assert sources[0]["value"] == value_encoded

    value_parts = [
        {"value": ANY},
        {"source": 0, "value": value_encoded},
        {"source": 0, "value": value_encoded},
        {"value": ".txt"},
    ]
    _assert_vulnerability(data, value_parts, "propagation_path_1_source_2_prop")


@pytest.mark.parametrize(
    "origin1, origin2",
    [
        ("taintsource1", "taintsource2"),
        #  ("taintsource", "taintsource"), TODO: invalid source pos
        ("1", "1"),
        (b"taintsource1", "taintsource2"),
        (b"taintsource1", b"taintsource2"),
        ("taintsource1", b"taintsource2"),
        (bytearray(b"taintsource1"), "taintsource2"),
        (bytearray(b"taintsource1"), bytearray(b"taintsource2")),
        ("taintsource1", bytearray(b"taintsource2")),
        (bytearray(b"taintsource1"), b"taintsource2"),
        (bytearray(b"taintsource1"), bytearray(b"taintsource2")),
        (b"taintsource1", bytearray(b"taintsource2")),
    ],
)
def test_propagation_path_2_origins_2_propagations(origin1, origin2, iast_context_defaults):
    mod = _iast_patched_module("tests.appsec.iast.fixtures.propagation_path")

    tainted_string_1 = taint_pyobject(origin1, source_name="path1", source_value=origin1, source_origin=OriginType.PATH)
    tainted_string_2 = taint_pyobject(
        origin2, source_name="path2", source_value=origin2, source_origin=OriginType.PARAMETER
    )
    mod.propagation_path_2_source_2_prop(tainted_string_1, tainted_string_2)

    span_report = _get_span_report()
    span_report.build_and_scrub_value_parts()
    data = span_report._to_dict()
    sources = data["sources"]

    assert len(sources) == 2
    source1_value_encoded = str(origin1, encoding="utf-8") if type(origin1) is not str else origin1
    assert sources[0]["name"] == "path1"
    assert sources[0]["origin"] == OriginType.PATH
    assert sources[0]["value"] == source1_value_encoded

    source2_value_encoded = str(origin2, encoding="utf-8") if type(origin2) is not str else origin2
    assert sources[1]["name"] == "path2"
    assert sources[1]["origin"] == OriginType.PARAMETER
    assert sources[1]["value"] == source2_value_encoded
    value_parts = [
        {"value": ANY},
        {"source": 0, "value": source1_value_encoded},
        {"source": 1, "value": source2_value_encoded},
        {"value": ".txt"},
    ]
    _assert_vulnerability(data, value_parts, "propagation_path_2_source_2_prop")


@pytest.mark.parametrize(
    "origin1, origin2",
    [
        ("taintsource1", "taintsource2"),
        # ("taintsource", "taintsource"), TODO: invalid source pos
        ("1", "1"),
        (b"taintsource1", "taintsource2"),
        # (b"taintsource", "taintsource"), TODO: invalid source pos
        (b"taintsource1", b"taintsource2"),
        ("taintsource1", b"taintsource2"),
        (bytearray(b"taintsource1"), "taintsource2"),
        (bytearray(b"taintsource1"), bytearray(b"taintsource2")),
        ("taintsource1", bytearray(b"taintsource2")),
        (bytearray(b"taintsource1"), b"taintsource2"),
        (bytearray(b"taintsource1"), bytearray(b"taintsource2")),
        (b"taintsource1", bytearray(b"taintsource2")),
    ],
)
def test_propagation_path_2_origins_3_propagation(origin1, origin2, iast_context_defaults):
    mod = _iast_patched_module("tests.appsec.iast.fixtures.propagation_path")

    tainted_string_1 = taint_pyobject(origin1, source_name="path1", source_value=origin1, source_origin=OriginType.PATH)
    tainted_string_2 = taint_pyobject(
        origin2, source_name="path2", source_value=origin2, source_origin=OriginType.PARAMETER
    )
    mod.propagation_path_3_prop(tainted_string_1, tainted_string_2)

    span_report = _get_span_report()
    span_report.build_and_scrub_value_parts()
    data = span_report._to_dict()
    sources = data["sources"]

    assert len(sources) == 2
    source1_value_encoded = str(origin1, encoding="utf-8") if type(origin1) is not str else origin1
    assert sources[0]["name"] == "path1"
    assert sources[0]["origin"] == OriginType.PATH
    assert sources[0]["value"] == source1_value_encoded

    source2_value_encoded = str(origin2, encoding="utf-8") if type(origin2) is not str else origin2
    assert sources[1]["name"] == "path2"
    assert sources[1]["origin"] == OriginType.PARAMETER
    assert sources[1]["value"] == source2_value_encoded

    value_parts = [
        {"value": ANY},
        {"source": 0, "value": source1_value_encoded},
        {"source": 1, "value": source2_value_encoded},
        {"value": "-"},
        {"source": 0, "value": source1_value_encoded},
        {"source": 1, "value": source2_value_encoded},
        {"value": "-"},
        {"source": 0, "value": source1_value_encoded},
        {"source": 1, "value": source2_value_encoded},
        {"value": ".txt"},
    ]
    _assert_vulnerability(data, value_parts, "propagation_path_3_prop")


@pytest.mark.parametrize(
    "origin1, origin2",
    [
        ("taintsource1", "taintsource2"),
        ("taintsource", "taintsource"),
        (b"taintsource1", "taintsource2"),
        (b"taintsource1", b"taintsource2"),
        ("taintsource1", b"taintsource2"),
        (bytearray(b"taintsource1"), "taintsource2"),
        (bytearray(b"taintsource1"), bytearray(b"taintsource2")),
        ("taintsource1", bytearray(b"taintsource2")),
        (bytearray(b"taintsource1"), b"taintsource2"),
        (bytearray(b"taintsource1"), bytearray(b"taintsource2")),
        (b"taintsource1", bytearray(b"taintsource2")),
    ],
)
def test_propagation_path_2_origins_5_propagation(origin1, origin2, iast_context_defaults):
    mod = _iast_patched_module("tests.appsec.iast.fixtures.propagation_path")

    tainted_string_1 = taint_pyobject(origin1, source_name="path1", source_value=origin1, source_origin=OriginType.PATH)
    tainted_string_2 = taint_pyobject(
        origin2, source_name="path2", source_value=origin2, source_origin=OriginType.PARAMETER
    )
    mod.propagation_path_5_prop(tainted_string_1, tainted_string_2)

    span_report = _get_span_report()
    span_report.build_and_scrub_value_parts()
    data = span_report._to_dict()
    sources = data["sources"]
    assert len(sources) == 1
    source1_value_encoded = str(origin1, encoding="utf-8") if type(origin1) is not str else origin1
    assert sources[0]["name"] == "path1"
    assert sources[0]["origin"] == OriginType.PATH
    assert sources[0]["value"] == source1_value_encoded

    value_parts = [{"value": ANY}, {"source": 0, "value": "aint"}, {"value": ".txt"}]
    _assert_vulnerability(data, value_parts, "propagation_path_5_prop")
