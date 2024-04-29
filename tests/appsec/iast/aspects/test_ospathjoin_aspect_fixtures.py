from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import Source
from ddtrace.appsec._iast._taint_tracking import TaintRange
from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking import taint_pyobject
from tests.appsec.iast.aspects.conftest import _iast_patched_module


mod = _iast_patched_module("tests.appsec.iast.fixtures.aspects.module_functions")


def test_join_tainted():
    string_input = taint_pyobject(
        pyobject="foo", source_name="first_element", source_value="foo", source_origin=OriginType.PARAMETER
    )
    result = mod.do_os_path_join(string_input, "bar")
    assert result == "foo/bar"
    assert get_tainted_ranges(result) == [TaintRange(0, 3, Source("first_element", "foo", OriginType.PARAMETER))]
