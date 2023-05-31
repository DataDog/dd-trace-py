import pytest

try:
    from tests.appsec.iast.aspects.conftest import _iast_patched_module
    from ddtrace.appsec.iast._taint_tracking import get_tainted_ranges
    from ddtrace.appsec.iast._taint_tracking import taint_pyobject
    from ddtrace.appsec.iast._taint_tracking import Source
    from ddtrace.appsec.iast._taint_tracking import OriginType
    from ddtrace.appsec.iast._taint_tracking import TaintRange
except (ImportError, AttributeError):
    pytest.skip("IAST not supported for this Python version", allow_module_level=True)

mod = _iast_patched_module("tests.appsec.iast.fixtures.aspects.str_methods")

_SOURCE1 = Source("test", "foobar", OriginType.PARAMETER)


def test_upper():
    from ddtrace.appsec.iast._taint_tracking import setup
    setup(bytes.join, bytearray.join)
    s = "foobar"
    assert not get_tainted_ranges(s)
    res = mod.do_upper(s)
    assert res == "FOOBAR"
    assert not get_tainted_ranges(res)

    s2 = "barbaz"
    s_tainted = taint_pyobject(s2, _SOURCE1)
    assert s_tainted == "barbaz"
    ranges = get_tainted_ranges(s_tainted)
    assert ranges == [TaintRange(0, 6, _SOURCE1)]
