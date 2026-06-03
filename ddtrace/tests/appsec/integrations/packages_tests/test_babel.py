from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted
from tests.appsec.iast.iast_utils import _iast_patched_module


mod = _iast_patched_module("tests.appsec.integrations.fixtures.patch_babel", should_patch_iast=True)


def test_babel_locale():
    value = mod.babel_locale()
    assert value == "OK:<NumberPattern '¤#,##0.00'>"
    assert not get_tainted_ranges(value)
    assert not is_pyobject_tainted(value)


def test_babel_to_python():
    value = mod.babel_to_python(1)
    assert value == "OK:one"
    assert not get_tainted_ranges(value)
    assert not is_pyobject_tainted(value)
    value = mod.babel_to_python(2)
    assert value == "OK:few"
    assert not get_tainted_ranges(value)
    assert not is_pyobject_tainted(value)
