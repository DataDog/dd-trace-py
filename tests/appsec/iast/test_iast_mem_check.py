import os

import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast._utils import _is_python_version_supported as python_supported_by_iast
from ddtrace.internal import core
from tests.appsec.iast.aspects.conftest import _iast_patched_module


FIXTURES_PATH = "tests/appsec/iast/fixtures/propagation_path.py"


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
    import psutil

    from ddtrace.appsec._iast._taint_tracking import OriginType
    from ddtrace.appsec._iast._taint_tracking import active_map_addreses_size
    from ddtrace.appsec._iast._taint_tracking import create_context
    from ddtrace.appsec._iast._taint_tracking import initializer_size
    from ddtrace.appsec._iast._taint_tracking import num_objects_tainted
    from ddtrace.appsec._iast._taint_tracking import reset_context
    from ddtrace.appsec._iast._taint_tracking import taint_pyobject

    start_memory = psutil.Process(os.getpid()).memory_info().rss

    mod = _iast_patched_module("tests.appsec.iast.fixtures.propagation_path")

    _num_objects_tainted = 0
    _active_map_addreses_size = 0
    _initializer_size = 0
    for _ in range(500):
        create_context()
        tainted_string_1 = taint_pyobject(
            origin1, source_name="path1", source_value=origin1, source_origin=OriginType.PATH
        )
        tainted_string_2 = taint_pyobject(
            origin2, source_name="path2", source_value=origin2, source_origin=OriginType.PARAMETER
        )
        mod.propagation_path_3_prop(tainted_string_1, tainted_string_2)

        span_report = core.get_item(IAST.CONTEXT_KEY, span=iast_span_defaults)
        assert span_report.sources
        assert span_report.vulnerabilities

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
        end_memory = psutil.Process(os.getpid()).memory_info().rss
        assert end_memory < start_memory * 1.05, "Memory increment to {} from {}".format(end_memory, start_memory)
