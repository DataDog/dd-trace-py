import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import Source
from ddtrace.appsec._iast._taint_tracking import TaintRange
from ddtrace.appsec._iast._taint_tracking import _aspect_ospathjoin
from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking import taint_pyobject


tainted_foo_slash = taint_pyobject(
    pyobject="/foo",
    source_name="test_ospathjoin",
    source_value="/foo",
    source_origin=OriginType.PARAMETER,
)

tainted_bar = taint_pyobject(
    pyobject="bar",
    source_name="test_ospathjoin",
    source_value="bar",
    source_origin=OriginType.PARAMETER,
)


def test_first_arg_nottainted_noslash():
    tainted_foo = taint_pyobject(
        pyobject="foo",
        source_name="test_ospathjoin",
        source_value="foo",
        source_origin=OriginType.PARAMETER,
    )

    tainted_bar = taint_pyobject(
        pyobject="bar",
        source_name="test_ospathjoin",
        source_value="bar",
        source_origin=OriginType.PARAMETER,
    )
    res = _aspect_ospathjoin("root", tainted_foo, "nottainted", tainted_bar, "alsonottainted")
    assert res == "root/foo/nottainted/bar/alsonottainted"
    assert get_tainted_ranges(res) == [
        TaintRange(5, 3, Source("test_ospathjoin", "foo", OriginType.PARAMETER)),
        TaintRange(20, 3, Source("test_ospathjoin", "bar", OriginType.PARAMETER)),
    ]


def test_later_arg_tainted_with_slash_then_ignore_previous():
    ignored_tainted_foo = taint_pyobject(
        pyobject="foo",
        source_name="test_ospathjoin",
        source_value="foo",
        source_origin=OriginType.PARAMETER,
    )

    tainted_slashbar = taint_pyobject(
        pyobject="/bar",
        source_name="test_ospathjoin",
        source_value="/bar",
        source_origin=OriginType.PARAMETER,
    )

    res = _aspect_ospathjoin("ignored", ignored_tainted_foo, "ignored_nottainted", tainted_slashbar, "alsonottainted")
    assert res == "/bar/alsonottainted"
    assert get_tainted_ranges(res) == [
        TaintRange(0, 4, Source("test_ospathjoin", "/bar", OriginType.PARAMETER)),
    ]


def test_first_arg_tainted_no_slash():
    tainted_foo = taint_pyobject(
        pyobject="foo",
        source_name="test_ospathjoin",
        source_value="foo",
        source_origin=OriginType.PARAMETER,
    )

    tainted_bar = taint_pyobject(
        pyobject="bar",
        source_name="test_ospathjoin",
        source_value="bar",
        source_origin=OriginType.PARAMETER,
    )

    res = _aspect_ospathjoin(tainted_foo, "nottainted", tainted_bar, "alsonottainted")
    assert res == "foo/nottainted/bar/alsonottainted"
    assert get_tainted_ranges(res) == [
        TaintRange(0, 3, Source("test_ospathjoin", "foo", OriginType.PARAMETER)),
        TaintRange(15, 3, Source("test_ospathjoin", "bar", OriginType.PARAMETER)),
    ]


def test_first_arg_tainted_with_slah():
    tainted_slashfoo = taint_pyobject(
        pyobject="/foo",
        source_name="test_ospathjoin",
        source_value="/foo",
        source_origin=OriginType.PARAMETER,
    )

    tainted_bar = taint_pyobject(
        pyobject="bar",
        source_name="test_ospathjoin",
        source_value="bar",
        source_origin=OriginType.PARAMETER,
    )

    res = _aspect_ospathjoin(tainted_slashfoo, "nottainted", tainted_bar, "alsonottainted")
    assert res == "/foo/nottainted/bar/alsonottainted"
    assert get_tainted_ranges(res) == [
        TaintRange(0, 4, Source("test_ospathjoin", "/foo", OriginType.PARAMETER)),
        TaintRange(16, 3, Source("test_ospathjoin", "bar", OriginType.PARAMETER)),
    ]


def test_single_arg_nottainted():
    res = _aspect_ospathjoin("nottainted")
    assert res == "nottainted"
    assert not get_tainted_ranges(res)

    res = _aspect_ospathjoin("/nottainted")
    assert res == "/nottainted"
    assert not get_tainted_ranges(res)


def test_single_arg_tainted():
    tainted_foo = taint_pyobject(
        pyobject="foo",
        source_name="test_ospathjoin",
        source_value="foo",
        source_origin=OriginType.PARAMETER,
    )
    res = _aspect_ospathjoin(tainted_foo)
    assert res == "foo"
    assert get_tainted_ranges(res) == [TaintRange(0, 3, Source("test_ospathjoin", "/foo", OriginType.PARAMETER))]

    tainted_slashfoo = taint_pyobject(
        pyobject="/foo",
        source_name="test_ospathjoin",
        source_value="/foo",
        source_origin=OriginType.PARAMETER,
    )
    res = _aspect_ospathjoin(tainted_slashfoo)
    assert res == "/foo"
    assert get_tainted_ranges(res) == [TaintRange(0, 4, Source("test_ospathjoin", "/foo", OriginType.PARAMETER))]


def test_last_slash_nottainted():
    tainted_foo = taint_pyobject(
        pyobject="foo",
        source_name="test_ospathjoin",
        source_value="foo",
        source_origin=OriginType.PARAMETER,
    )

    res = _aspect_ospathjoin("root", tainted_foo, "/nottainted")
    assert res == "/nottainted"
    assert not get_tainted_ranges(res)


def test_last_slash_tainted():
    tainted_foo = taint_pyobject(
        pyobject="foo",
        source_name="test_ospathjoin",
        source_value="foo",
        source_origin=OriginType.PARAMETER,
    )

    tainted_slashbar = taint_pyobject(
        pyobject="/bar",
        source_name="test_ospathjoin",
        source_value="/bar",
        source_origin=OriginType.PARAMETER,
    )
    res = _aspect_ospathjoin("root", tainted_foo, "nottainted", tainted_slashbar)
    assert res == "/bar"
    assert get_tainted_ranges(res) == [TaintRange(0, 4, Source("test_ospathjoin", "/bar", OriginType.PARAMETER))]


def test_wrong_arg():
    with pytest.raises(TypeError):
        _ = _aspect_ospathjoin("root", 42, "foobar")


def test_bytes_nottainted():
    res = _aspect_ospathjoin(b"nottainted", b"alsonottainted")
    assert res == b"nottainted/alsonottainted"


def test_bytes_tainted():
    tainted_foo = taint_pyobject(
        pyobject=b"foo",
        source_name="test_ospathjoin",
        source_value=b"foo",
        source_origin=OriginType.PARAMETER,
    )
    res = _aspect_ospathjoin(tainted_foo, b"nottainted")
    assert res == b"foo/nottainted"
    assert get_tainted_ranges(res) == [TaintRange(0, 3, Source("test_ospathjoin", b"foo", OriginType.PARAMETER))]

    tainted_slashfoo = taint_pyobject(
        pyobject=b"/foo",
        source_name="test_ospathjoin",
        source_value=b"/foo",
        source_origin=OriginType.PARAMETER,
    )

    res = _aspect_ospathjoin(tainted_slashfoo, b"nottainted")
    assert res == b"/foo/nottainted"
    assert get_tainted_ranges(res) == [TaintRange(0, 4, Source("test_ospathjoin", b"/foo", OriginType.PARAMETER))]

    res = _aspect_ospathjoin(b"nottainted_ignore", b"alsoignored", tainted_slashfoo)
    assert res == b"/foo"
    assert get_tainted_ranges(res) == [TaintRange(0, 4, Source("test_ospathjoin", b"/foo", OriginType.PARAMETER))]


def test_empty():
    res = _aspect_ospathjoin("")
    assert res == ""


def test_noparams():
    with pytest.raises(TypeError):
        _ = _aspect_ospathjoin()
