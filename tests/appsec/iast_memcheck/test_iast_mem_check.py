import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import active_map_addreses_size
from ddtrace.appsec._iast._taint_tracking import create_context
from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking import initializer_size
from ddtrace.appsec._iast._taint_tracking import num_objects_tainted
from ddtrace.appsec._iast._taint_tracking import reset_context
from ddtrace.appsec._iast._taint_tracking import taint_pyobject
from ddtrace.appsec._iast._utils import _is_python_version_supported as python_supported_by_iast
from ddtrace.internal import core
from tests.appsec.iast.aspects.conftest import _iast_patched_module
from tests.appsec.iast.fixtures.propagation_path import propagation_memory_check
from tests.appsec.iast.fixtures.stacktrace import func_1


FIXTURES_PATH = "tests/appsec/iast/fixtures/propagation_path.py"


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
@pytest.mark.limit_memory("1.3 MB")
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
def test_propagation_memory_check(origin1, origin2, iast_span_defaults):
    expected_result = propagation_memory_check(origin1, origin2)
    mod = _iast_patched_module("tests.appsec.iast.fixtures.propagation_path")

    _num_objects_tainted = 0
    _active_map_addreses_size = 0
    _initializer_size = 0
    for _ in range(100):
        create_context()
        tainted_string_1 = taint_pyobject(
            origin1, source_name="path1", source_value=origin1, source_origin=OriginType.PATH
        )
        tainted_string_2 = taint_pyobject(
            origin2, source_name="path2", source_value=origin2, source_origin=OriginType.PARAMETER
        )
        result = mod.propagation_memory_check(tainted_string_1, tainted_string_2)

        assert result == expected_result

        span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
        assert len(span_report.sources) > 0
        assert len(span_report.vulnerabilities) > 0
        assert len(get_tainted_ranges(result)) == 6

        if _num_objects_tainted == 0:
            _num_objects_tainted = num_objects_tainted()
            assert _num_objects_tainted > 0
        if _active_map_addreses_size == 0:
            _active_map_addreses_size = active_map_addreses_size()
            assert _active_map_addreses_size > 0
        if _initializer_size == 0:
            _initializer_size = initializer_size()
            assert _initializer_size > 0

        assert _num_objects_tainted == num_objects_tainted()
        assert _active_map_addreses_size == active_map_addreses_size()
        assert _initializer_size == initializer_size()
        reset_context()


@pytest.mark.limit_memory("9.4 KB")
def test_stacktrace_memory_check():
    """2.1KiB is enough but riot allocate more bytes"""
    for _ in range(50000):
        frame_info = func_1("", "2", "3")
        if not frame_info:
            pytest.fail("No stacktrace")

        file_name, line_number = frame_info
        assert file_name
        assert line_number > 0


@pytest.mark.limit_memory("2.1 KB")
def test_stacktrace_memory_empty_byte_check():
    """2.1KiB is enough but riot allocate more bytes"""
    for _ in range(50000):
        frame_info = func_1("empty_byte", "2", "3")
        if not frame_info:
            pytest.fail("No stacktrace")

        file_name, line_number = frame_info
        assert file_name
        assert line_number > 0


@pytest.mark.limit_memory("9.7 KB")
def test_stacktrace_memory_empty_string_check():
    """2.1KiB is enough but riot allocate more bytes"""
    for _ in range(50000):
        frame_info = func_1("empty_string", "2", "3")
        if not frame_info:
            pytest.fail("No stacktrace")

        file_name, line_number = frame_info
        assert file_name
        assert line_number > 0


@pytest.mark.limit_memory("9.7 KB")
def test_stacktrace_memory_random_string_check():
    """2.1KiB is enough but riot allocate more bytes"""
    for _ in range(50000):
        frame_info = func_1("random_string", "2", "3")
        if not frame_info:
            pytest.fail("No stacktrace")

        file_name, line_number = frame_info
        assert file_name == ""
        assert line_number == 0
