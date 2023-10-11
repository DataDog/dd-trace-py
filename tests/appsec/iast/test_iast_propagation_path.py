from mock.mock import ANY
import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec.iast._utils import _is_python_version_supported as python_supported_by_iast
from ddtrace.appsec.iast.constants import VULN_PATH_TRAVERSAL
from ddtrace.internal import core
from tests.appsec.iast.aspects.conftest import _iast_patched_module
from tests.appsec.iast.iast_utils import get_line_and_hash


FIXTURES_PATH = "tests/appsec/iast/fixtures/propagation_path.py"


def _assert_vulnerability(span_report, value_parts, file_line_label):
    vulnerability = list(span_report.vulnerabilities)[0]
    assert vulnerability.type == VULN_PATH_TRAVERSAL
    assert vulnerability.evidence.valueParts == value_parts
    assert vulnerability.evidence.value is None
    assert vulnerability.evidence.pattern is None
    assert vulnerability.evidence.redacted is None

    line, hash_value = get_line_and_hash(file_line_label, VULN_PATH_TRAVERSAL, filename=FIXTURES_PATH)
    assert vulnerability.location.path == FIXTURES_PATH
    assert vulnerability.location.line == line
    assert vulnerability.hash == hash_value


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
@pytest.mark.parametrize(
    "origin1",
    [
        ("taintsource"),
        ("1"),
        (b"taintsource1"),
        (bytearray(b"taintsource1")),
    ],
)
def test_propagation_path_1_origin_1_propagation(origin1, iast_span_defaults):
    from ddtrace.appsec.iast._taint_tracking import OriginType
    from ddtrace.appsec.iast._taint_tracking import taint_pyobject

    mod = _iast_patched_module("tests.appsec.iast.fixtures.propagation_path")

    tainted_string = taint_pyobject(origin1, source_name="path", source_value=origin1, source_origin=OriginType.PATH)
    mod.propagation_path_1_source_1_prop(tainted_string)

    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
    source = span_report.sources[0]
    source_value_encoded = str(origin1, encoding="utf-8") if type(origin1) is not str else origin1

    assert source.name == "path"
    assert source.origin == OriginType.PATH
    assert source.value == source_value_encoded

    value_parts = [
        {"value": ANY},
        {"source": 0, "value": source_value_encoded},
        {"value": ".txt"},
    ]
    _assert_vulnerability(span_report, value_parts, "propagation_path_1_source_1_prop")


@pytest.mark.skip(reason="aspect add fails when var1 + var1")
@pytest.mark.parametrize(
    "origin1",
    [
        ("taintsource"),
        ("1"),
        (b"taintsource1"),
        (bytearray(b"taintsource1")),
    ],
)
def test_propagation_path_1_origins_2_propagations(origin1, iast_span_defaults):
    from ddtrace.appsec.iast._taint_tracking import OriginType
    from ddtrace.appsec.iast._taint_tracking import taint_pyobject

    mod = _iast_patched_module("tests.appsec.iast.fixtures.propagation_path")

    tainted_string_1 = taint_pyobject(origin1, source_name="path1", source_value=origin1, source_origin=OriginType.PATH)

    mod.propagation_path_1_source_2_prop(tainted_string_1)

    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)

    sources = span_report.sources
    assert len(sources) == 1
    assert sources[0].name == "path1"
    assert sources[0].origin == OriginType.PATH
    assert sources[0].value == str(origin1, encoding="utf-8") if type(origin1) is not str else origin1

    value_parts = [
        {"value": ANY},
        {"source": 0, "value": str(origin1)},
        {"source": 0, "value": str(origin1)},
        {"value": ".txt"},
    ]
    _assert_vulnerability(span_report, value_parts, "propagation_path_1_source_2_prop")


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
@pytest.mark.parametrize(
    "origin1, origin2",
    [
        ("taintsource1", "taintsource2"),
        ("taintsource", "taintsource"),
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
def test_propagation_path_2_origins_2_propagations(origin1, origin2, iast_span_defaults):
    from ddtrace.appsec.iast._taint_tracking import OriginType
    from ddtrace.appsec.iast._taint_tracking import taint_pyobject

    mod = _iast_patched_module("tests.appsec.iast.fixtures.propagation_path")

    tainted_string_1 = taint_pyobject(origin1, source_name="path1", source_value=origin1, source_origin=OriginType.PATH)
    tainted_string_2 = taint_pyobject(
        origin2, source_name="path2", source_value=origin2, source_origin=OriginType.PARAMETER
    )
    mod.propagation_path_2_source_2_prop(tainted_string_1, tainted_string_2)

    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)

    sources = span_report.sources
    assert len(sources) == 2
    source1_value_encoded = str(origin1, encoding="utf-8") if type(origin1) is not str else origin1
    assert sources[0].name == "path1"
    assert sources[0].origin == OriginType.PATH
    assert sources[0].value == source1_value_encoded

    source2_value_encoded = str(origin2, encoding="utf-8") if type(origin2) is not str else origin2
    assert sources[1].name == "path2"
    assert sources[1].origin == OriginType.PARAMETER
    assert sources[1].value == source2_value_encoded

    value_parts = [
        {"value": ANY},
        {"source": 0, "value": source1_value_encoded},
        {"source": 1, "value": source2_value_encoded},
        {"value": ".txt"},
    ]
    _assert_vulnerability(span_report, value_parts, "propagation_path_2_source_2_prop")


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
@pytest.mark.parametrize(
    "origin1, origin2",
    [
        ("taintsource1", "taintsource2"),
        ("taintsource", "taintsource"),
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
def test_propagation_path_2_origins_3_propagation(origin1, origin2, iast_span_defaults):
    from ddtrace.appsec.iast._taint_tracking import OriginType
    from ddtrace.appsec.iast._taint_tracking import taint_pyobject

    mod = _iast_patched_module("tests.appsec.iast.fixtures.propagation_path")

    tainted_string_1 = taint_pyobject(origin1, source_name="path1", source_value=origin1, source_origin=OriginType.PATH)
    tainted_string_2 = taint_pyobject(
        origin2, source_name="path2", source_value=origin2, source_origin=OriginType.PARAMETER
    )
    mod.propagation_path_3_prop(tainted_string_1, tainted_string_2)

    span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)

    sources = span_report.sources
    assert len(sources) == 2
    source1_value_encoded = str(origin1, encoding="utf-8") if type(origin1) is not str else origin1
    assert sources[0].name == "path1"
    assert sources[0].origin == OriginType.PATH
    assert sources[0].value == source1_value_encoded

    source2_value_encoded = str(origin2, encoding="utf-8") if type(origin2) is not str else origin2
    assert sources[1].name == "path2"
    assert sources[1].origin == OriginType.PARAMETER
    assert sources[1].value == source2_value_encoded

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
    _assert_vulnerability(span_report, value_parts, "propagation_path_3_prop")
