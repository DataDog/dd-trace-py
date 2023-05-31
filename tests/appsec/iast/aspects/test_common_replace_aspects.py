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


def test_upper():
    # s = "foobar"
    # assert not get_tainted_ranges(s)
    # res = mod.do_upper(s)
    # assert res == "FOOBAR"
    # assert not get_tainted_ranges(res)

    s2 = "barbaz"
    s_tainted = taint_pyobject(s2, Source("test", "foobar", OriginType.PARAMETER))
    # assert s_tainted == "foobar"
    # ranges = get_tainted_ranges(s_tainted)
