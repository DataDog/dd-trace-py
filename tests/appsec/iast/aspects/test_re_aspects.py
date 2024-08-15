import re

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import Source
from ddtrace.appsec._iast._taint_tracking import TaintRange
from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking import taint_pyobject
from ddtrace.appsec._iast._taint_tracking.aspects import re_sub_aspect


def test_re_sub_aspect_tainted_string():
    tainted_foobarbaz = taint_pyobject(
        pyobject="/foo/bar/baz.jpg",
        source_name="test_ospath",
        source_value="/foo/bar/baz.jpg",
        source_origin=OriginType.PARAMETER,
    )

    re_slash = re.compile(r"/")

    res_str = re_sub_aspect(re_slash.sub, 1, re_slash, "_", tainted_foobarbaz)
    assert res_str == "_foo_bar_baz.jpg"
    assert get_tainted_ranges(res_str) == [
        TaintRange(0, len(res_str), Source("test_re_sub_aspect", tainted_foobarbaz, OriginType.PARAMETER)),
    ]


def test_re_sub_aspect_tainted_repl():
    tainted___ = taint_pyobject(
        pyobject="___",
        source_name="test_re_sub_aspect_tainted_repl",
        source_value="___",
        source_origin=OriginType.PARAMETER,
    )

    re_slash = re.compile(r"/")

    res_str = re_sub_aspect(re_slash.sub, 1, re_slash, tainted___, "foo/bar/baz")
    assert res_str == "foo___bar___baz"
    assert get_tainted_ranges(res_str) == [
        TaintRange(0, len(res_str), Source("test_re_sub_aspect_tainted_repl", tainted___, OriginType.PARAMETER)),
    ]
