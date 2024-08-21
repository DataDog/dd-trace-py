import re

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import Source
from ddtrace.appsec._iast._taint_tracking import TaintRange
from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking import taint_pyobject
from ddtrace.appsec._iast._taint_tracking.aspects import re_findall_aspect
from ddtrace.appsec._iast._taint_tracking.aspects import re_sub_aspect
from ddtrace.appsec._iast._taint_tracking.aspects import re_subn_aspect
from ddtrace.appsec._iast._taint_tracking.aspects import split_aspect


def test_re_findall_aspect_tainted_string():
    tainted_foobarbaz = taint_pyobject(
        pyobject="/foo/bar/baaz.jpeg",
        source_name="test_re_findall_aspect_tainted_string",
        source_value="/foo/bar/baaz.jpeg",
        source_origin=OriginType.PARAMETER,
    )

    re_slash = re.compile(r"[/.][a-z]*")

    res_list = re_findall_aspect(None, 1, re_slash, tainted_foobarbaz)
    assert res_list == ["/foo", "/bar", "/baaz", ".jpeg"]
    for i in res_list:
        assert get_tainted_ranges(i) == [
            TaintRange(0, len(i), Source("test_re_sub_aspect_tainted_string", tainted_foobarbaz, OriginType.PARAMETER)),
        ]


def test_re_sub_aspect_tainted_string():
    tainted_foobarbaz = taint_pyobject(
        pyobject="/foo/bar/baz.jpg",
        source_name="test_re_sub_aspect_tainted_string",
        source_value="/foo/bar/baz.jpg",
        source_origin=OriginType.PARAMETER,
    )

    re_slash = re.compile(r"/")

    res_str = re_sub_aspect(None, 1, re_slash, "_", tainted_foobarbaz)
    assert res_str == "_foo_bar_baz.jpg"
    assert get_tainted_ranges(res_str) == [
        TaintRange(
            0, len(res_str), Source("test_re_sub_aspect_tainted_string", tainted_foobarbaz, OriginType.PARAMETER)
        ),
    ]


def test_re_sub_aspect_tainted_repl():
    tainted___ = taint_pyobject(
        pyobject="___",
        source_name="test_re_sub_aspect_tainted_repl",
        source_value="___",
        source_origin=OriginType.PARAMETER,
    )

    re_slash = re.compile(r"/")

    res_str = re_sub_aspect(None, 1, re_slash, tainted___, "foo/bar/baz")
    assert res_str == "foo___bar___baz"
    assert get_tainted_ranges(res_str) == [
        TaintRange(0, len(res_str), Source("test_re_sub_aspect_tainted_repl", tainted___, OriginType.PARAMETER)),
    ]


def test_re_subn_aspect_tainted_string():
    tainted_foobarbaz = taint_pyobject(
        pyobject="/foo/bar/baz.jpg",
        source_name="test_re_subn_aspect_tainted_string",
        source_value="/foo/bar/baz.jpg",
        source_origin=OriginType.PARAMETER,
    )

    re_slash = re.compile(r"/")

    res_str, number = re_subn_aspect(None, 1, re_slash, "_", tainted_foobarbaz)
    assert res_str == "_foo_bar_baz.jpg"
    assert number == 3
    assert get_tainted_ranges(res_str) == [
        TaintRange(
            0, len(res_str), Source("test_re_subn_aspect_tainted_string", tainted_foobarbaz, OriginType.PARAMETER)
        ),
    ]


def test_re_subn_aspect_tainted_repl():
    tainted___ = taint_pyobject(
        pyobject="___",
        source_name="test_re_subn_aspect_tainted_repl",
        source_value="___",
        source_origin=OriginType.PARAMETER,
    )

    re_slash = re.compile(r"/")

    res_str, number = re_subn_aspect(None, 1, re_slash, tainted___, "foo/bar/baz")
    assert res_str == "foo___bar___baz"
    assert number == 2
    assert get_tainted_ranges(res_str) == [
        TaintRange(0, len(res_str), Source("test_re_subn_aspect_tainted_repl", tainted___, OriginType.PARAMETER)),
    ]


def test_re_split_aspect_tainted_string_re_object():
    tainted_foobarbaz = taint_pyobject(
        pyobject="/foo/bar/baz.jpg",
        source_name="test_re_split_aspect_tainted_string",
        source_value="/foo/bar/baz.jpg",
        source_origin=OriginType.PARAMETER,
    )

    re_slash = re.compile(r"/")

    res_list = split_aspect(None, 1, re_slash, tainted_foobarbaz)
    assert res_list == ["", "foo", "bar", "baz.jpg"]
    for res_str in res_list:
        if len(res_str):
            assert get_tainted_ranges(res_str) == [
                TaintRange(
                    0,
                    len(res_str),
                    Source("test_re_split_aspect_tainted_string", tainted_foobarbaz, OriginType.PARAMETER),
                ),
            ]
        else:
            assert not is_pyobject_tainted(res_str)


def test_re_split_aspect_tainted_string_re_module():
    tainted_foobarbaz = taint_pyobject(
        pyobject="/foo/bar/baz.jpg",
        source_name="test_re_split_aspect_tainted_string",
        source_value="/foo/bar/baz.jpg",
        source_origin=OriginType.PARAMETER,
    )

    res_list = split_aspect(None, 1, re, r"/", tainted_foobarbaz)
    assert res_list == ["", "foo", "bar", "baz.jpg"]
    for res_str in res_list:
        if len(res_str):
            assert get_tainted_ranges(res_str) == [
                TaintRange(
                    0,
                    len(res_str),
                    Source("test_re_split_aspect_tainted_string", tainted_foobarbaz, OriginType.PARAMETER),
                ),
            ]
        else:
            assert not is_pyobject_tainted(res_str)
