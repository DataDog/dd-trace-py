from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
from ddtrace.appsec._iast._taint_utils import LazyTaintList
from tests.appsec.iast.iast_utils import _iast_patched_module


mod = _iast_patched_module("tests.appsec.integrations.fixtures.patch_psycopg2", should_patch_iast=True)


def test_adapt_list():
    obj_list = [1, "word", True]

    value = mod.adapt_list(obj_list)
    assert value == b"r-ARRAY[1,'word',true]"
    assert not get_tainted_ranges(value)
    assert not is_pyobject_tainted(value)


def test_lazy_taint_list():
    obj_list = [1, "word", True]
    lazy_list = LazyTaintList(obj_list, origins=(OriginType.PARAMETER, OriginType.PARAMETER))

    value = mod.adapt_list(lazy_list)
    assert value == b"r-ARRAY[1,'word',true]"
    assert get_tainted_ranges(value)
    assert is_pyobject_tainted(value)
