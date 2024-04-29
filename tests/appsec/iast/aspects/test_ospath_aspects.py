import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import Source
from ddtrace.appsec._iast._taint_tracking import TaintRange
from ddtrace.appsec._iast._taint_tracking import _aspect_ospathjoin
from ddtrace.appsec._iast._taint_tracking import _aspect_ospathbasename
from ddtrace.appsec._iast._taint_tracking import _aspect_ospathdirname
from ddtrace.appsec._iast._taint_tracking import _aspect_ospathnormcase
from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking import taint_pyobject


def test_ospathjoin_first_arg_nottainted_noslash():
    tainted_foo = taint_pyobject(
        pyobject="foo",
        source_name="test_ospath",
        source_value="foo",
        source_origin=OriginType.PARAMETER,
    )

    tainted_bar = taint_pyobject(
        pyobject="bar",
        source_name="test_ospath",
        source_value="bar",
        source_origin=OriginType.PARAMETER,
    )
    res = _aspect_ospathjoin("root", tainted_foo, "nottainted", tainted_bar, "alsonottainted")
    assert res == "root/foo/nottainted/bar/alsonottainted"
    assert get_tainted_ranges(res) == [
        TaintRange(5, 3, Source("test_ospath", "foo", OriginType.PARAMETER)),
        TaintRange(20, 3, Source("test_ospath", "bar", OriginType.PARAMETER)),
    ]


def test_ospathjoin_later_arg_tainted_with_slash_then_ignore_previous():
    ignored_tainted_foo = taint_pyobject(
        pyobject="foo",
        source_name="test_ospath",
        source_value="foo",
        source_origin=OriginType.PARAMETER,
    )

    tainted_slashbar = taint_pyobject(
        pyobject="/bar",
        source_name="test_ospath",
        source_value="/bar",
        source_origin=OriginType.PARAMETER,
    )

    res = _aspect_ospathjoin("ignored", ignored_tainted_foo, "ignored_nottainted", tainted_slashbar, "alsonottainted")
    assert res == "/bar/alsonottainted"
    assert get_tainted_ranges(res) == [
        TaintRange(0, 4, Source("test_ospath", "/bar", OriginType.PARAMETER)),
    ]


def test_ospathjoin_first_arg_tainted_no_slash():
    tainted_foo = taint_pyobject(
        pyobject="foo",
        source_name="test_ospath",
        source_value="foo",
        source_origin=OriginType.PARAMETER,
    )

    tainted_bar = taint_pyobject(
        pyobject="bar",
        source_name="test_ospath",
        source_value="bar",
        source_origin=OriginType.PARAMETER,
    )

    res = _aspect_ospathjoin(tainted_foo, "nottainted", tainted_bar, "alsonottainted")
    assert res == "foo/nottainted/bar/alsonottainted"
    assert get_tainted_ranges(res) == [
        TaintRange(0, 3, Source("test_ospath", "foo", OriginType.PARAMETER)),
        TaintRange(15, 3, Source("test_ospath", "bar", OriginType.PARAMETER)),
    ]


def test_ospathjoin_first_arg_tainted_with_slash():
    tainted_slashfoo = taint_pyobject(
        pyobject="/foo",
        source_name="test_ospath",
        source_value="/foo",
        source_origin=OriginType.PARAMETER,
    )

    tainted_bar = taint_pyobject(
        pyobject="bar",
        source_name="test_ospath",
        source_value="bar",
        source_origin=OriginType.PARAMETER,
    )

    res = _aspect_ospathjoin(tainted_slashfoo, "nottainted", tainted_bar, "alsonottainted")
    assert res == "/foo/nottainted/bar/alsonottainted"
    assert get_tainted_ranges(res) == [
        TaintRange(0, 4, Source("test_ospath", "/foo", OriginType.PARAMETER)),
        TaintRange(16, 3, Source("test_ospath", "bar", OriginType.PARAMETER)),
    ]


def test_ospathjoin_single_arg_nottainted():
    res = _aspect_ospathjoin("nottainted")
    assert res == "nottainted"
    assert not get_tainted_ranges(res)

    res = _aspect_ospathjoin("/nottainted")
    assert res == "/nottainted"
    assert not get_tainted_ranges(res)


def test_ospathjoin_single_arg_tainted():
    tainted_foo = taint_pyobject(
        pyobject="foo",
        source_name="test_ospath",
        source_value="foo",
        source_origin=OriginType.PARAMETER,
    )
    res = _aspect_ospathjoin(tainted_foo)
    assert res == "foo"
    assert get_tainted_ranges(res) == [TaintRange(0, 3, Source("test_ospath", "/foo", OriginType.PARAMETER))]

    tainted_slashfoo = taint_pyobject(
        pyobject="/foo",
        source_name="test_ospath",
        source_value="/foo",
        source_origin=OriginType.PARAMETER,
    )
    res = _aspect_ospathjoin(tainted_slashfoo)
    assert res == "/foo"
    assert get_tainted_ranges(res) == [TaintRange(0, 4, Source("test_ospath", "/foo", OriginType.PARAMETER))]


def test_ospathjoin_last_slash_nottainted():
    tainted_foo = taint_pyobject(
        pyobject="foo",
        source_name="test_ospath",
        source_value="foo",
        source_origin=OriginType.PARAMETER,
    )

    res = _aspect_ospathjoin("root", tainted_foo, "/nottainted")
    assert res == "/nottainted"
    assert not get_tainted_ranges(res)


def test_ospathjoin_last_slash_tainted():
    tainted_foo = taint_pyobject(
        pyobject="foo",
        source_name="test_ospath",
        source_value="foo",
        source_origin=OriginType.PARAMETER,
    )

    tainted_slashbar = taint_pyobject(
        pyobject="/bar",
        source_name="test_ospath",
        source_value="/bar",
        source_origin=OriginType.PARAMETER,
    )
    res = _aspect_ospathjoin("root", tainted_foo, "nottainted", tainted_slashbar)
    assert res == "/bar"
    assert get_tainted_ranges(res) == [TaintRange(0, 4, Source("test_ospath", "/bar", OriginType.PARAMETER))]


def test_ospathjoin_wrong_arg():
    with pytest.raises(TypeError):
        _ = _aspect_ospathjoin("root", 42, "foobar")


def test_ospathjoin_bytes_nottainted():
    res = _aspect_ospathjoin(b"nottainted", b"alsonottainted")
    assert res == b"nottainted/alsonottainted"


def test_ospathjoin_bytes_tainted():
    tainted_foo = taint_pyobject(
        pyobject=b"foo",
        source_name="test_ospath",
        source_value=b"foo",
        source_origin=OriginType.PARAMETER,
    )
    res = _aspect_ospathjoin(tainted_foo, b"nottainted")
    assert res == b"foo/nottainted"
    assert get_tainted_ranges(res) == [TaintRange(0, 3, Source("test_ospath", b"foo", OriginType.PARAMETER))]

    tainted_slashfoo = taint_pyobject(
        pyobject=b"/foo",
        source_name="test_ospath",
        source_value=b"/foo",
        source_origin=OriginType.PARAMETER,
    )

    res = _aspect_ospathjoin(tainted_slashfoo, b"nottainted")
    assert res == b"/foo/nottainted"
    assert get_tainted_ranges(res) == [TaintRange(0, 4, Source("test_ospath", b"/foo", OriginType.PARAMETER))]

    res = _aspect_ospathjoin(b"nottainted_ignore", b"alsoignored", tainted_slashfoo)
    assert res == b"/foo"
    assert get_tainted_ranges(res) == [TaintRange(0, 4, Source("test_ospath", b"/foo", OriginType.PARAMETER))]


def test_ospathjoin_empty():
    res = _aspect_ospathjoin("")
    assert res == ""


def test_ospathjoin_noparams():
    with pytest.raises(TypeError):
        _ = _aspect_ospathjoin()


def test_ospathbasename_tainted_normal():
    tainted_foobarbaz = taint_pyobject(
        pyobject="/foo/bar/baz",
        source_name="test_ospath",
        source_value="/foo/bar/baz",
        source_origin=OriginType.PARAMETER,
    )

    res = _aspect_ospathbasename(tainted_foobarbaz)
    assert res == "baz"
    assert get_tainted_ranges(res) == [TaintRange(0, 3, Source("test_ospath", "/foo/bar/baz", OriginType.PARAMETER))]


def test_ospathbasename_tainted_empty():
    tainted_empty = taint_pyobject(
        pyobject="",
        source_name="test_ospath",
        source_value="",
        source_origin=OriginType.PARAMETER,
    )

    res = _aspect_ospathbasename(tainted_empty)
    assert res == ""
    assert not get_tainted_ranges(res)


def test_ospathbasename_nottainted():
    res = _aspect_ospathbasename("/foo/bar/baz")
    assert res == "baz"
    assert not get_tainted_ranges(res)


def test_ospathbasename_wrong_arg():
    with pytest.raises(TypeError):
        _ = _aspect_ospathbasename(42)


def test_ospathbasename_bytes_tainted():
    tainted_foobarbaz = taint_pyobject(
        pyobject=b"/foo/bar/baz",
        source_name="test_ospath",
        source_value=b"/foo/bar/baz",
        source_origin=OriginType.PARAMETER,
    )

    res = _aspect_ospathbasename(tainted_foobarbaz)
    assert res == b"baz"
    assert get_tainted_ranges(res) == [TaintRange(0, 3, Source("test_ospath", b"/foo/bar/baz", OriginType.PARAMETER))]


def test_ospathbasename_bytes_nottainted():
    res = _aspect_ospathbasename(b"/foo/bar/baz")
    assert res == b"baz"
    assert not get_tainted_ranges(res)


def test_ospathbasename_single_slash_tainted():
    tainted_slash = taint_pyobject(
        pyobject="/",
        source_name="test_ospath",
        source_value="/",
        source_origin=OriginType.PARAMETER,
    )
    res = _aspect_ospathbasename(tainted_slash)
    assert res == ""
    assert not get_tainted_ranges(res)


def test_ospathbasename_nottainted_empty():
    res = _aspect_ospathbasename("")
    assert res == ""
    assert not get_tainted_ranges(res)


def test_ospathnormcase_tainted_normal():
    tainted_foobarbaz = taint_pyobject(
        pyobject="/foo/bar/baz",
        source_name="test_ospath",
        source_value="/foo/bar/baz",
        source_origin=OriginType.PARAMETER,
    )

    res = _aspect_ospathnormcase(tainted_foobarbaz)
    assert res == "/foo/bar/baz"
    assert get_tainted_ranges(res) == [TaintRange(0, 12, Source("test_ospath", "/foo/bar/baz", OriginType.PARAMETER))]


def test_ospathnormcase_tainted_empty():
    tainted_empty = taint_pyobject(
        pyobject="",
        source_name="test_ospath",
        source_value="",
        source_origin=OriginType.PARAMETER,
    )

    res = _aspect_ospathnormcase(tainted_empty)
    assert res == ""
    assert not get_tainted_ranges(res)


def test_ospathnormcase_nottainted():
    res = _aspect_ospathnormcase("/foo/bar/baz")
    assert res == "/foo/bar/baz"
    assert not get_tainted_ranges(res)


def test_ospathnormcase_wrong_arg():
    with pytest.raises(TypeError):
        _ = _aspect_ospathnormcase(42)


def test_ospathnormcase_bytes_tainted():
    tainted_foobarbaz = taint_pyobject(
        pyobject=b"/foo/bar/baz",
        source_name="test_ospath",
        source_value=b"/foo/bar/baz",
        source_origin=OriginType.PARAMETER,
    )

    res = _aspect_ospathnormcase(tainted_foobarbaz)
    assert res == b"/foo/bar/baz"
    assert get_tainted_ranges(res) == [TaintRange(0, 12, Source("test_ospath", b"/foo/bar/baz", OriginType.PARAMETER))]


def test_ospathnormcase_bytes_nottainted():
    res = _aspect_ospathnormcase(b"/foo/bar/baz")
    assert res == b"/foo/bar/baz"
    assert not get_tainted_ranges(res)


def test_ospathnormcase_single_slash_tainted():
    tainted_slash = taint_pyobject(
        pyobject="/",
        source_name="test_ospath",
        source_value="/",
        source_origin=OriginType.PARAMETER,
    )
    res = _aspect_ospathnormcase(tainted_slash)
    assert res == "/"
    assert get_tainted_ranges(res) == [TaintRange(0, 1, Source("test_ospath", "/", OriginType.PARAMETER))]


def test_ospathnormcase_nottainted_empty():
    res = _aspect_ospathnormcase("")
    assert res == ""
    assert not get_tainted_ranges(res)


def test_ospathdirname_tainted_normal():
    tainted_foobarbaz = taint_pyobject(
        pyobject="/foo/bar/baz",
        source_name="test_ospath",
        source_value="/foo/bar/baz",
        source_origin=OriginType.PARAMETER,
    )

    res = _aspect_ospathdirname(tainted_foobarbaz)
    assert res == "/foo/bar"
    assert get_tainted_ranges(res) == [TaintRange(0, 8, Source("test_ospath", "/foo/bar/baz", OriginType.PARAMETER))]


def test_ospathdirname_tainted_empty():
    tainted_empty = taint_pyobject(
        pyobject="",
        source_name="test_ospath",
        source_value="",
        source_origin=OriginType.PARAMETER,
    )

    res = _aspect_ospathdirname(tainted_empty)
    assert res == ""
    assert not get_tainted_ranges(res)


def test_ospathdirname_nottainted():
    res = _aspect_ospathdirname("/foo/bar/baz")
    assert res == "/foo/bar"
    assert not get_tainted_ranges(res)


def test_ospathdirname_wrong_arg():
    with pytest.raises(TypeError):
        _ = _aspect_ospathdirname(42)


def test_ospathdirname_bytes_tainted():
    tainted_foobarbaz = taint_pyobject(
        pyobject=b"/foo/bar/baz",
        source_name="test_ospath",
        source_value=b"/foo/bar/baz",
        source_origin=OriginType.PARAMETER,
    )

    res = _aspect_ospathdirname(tainted_foobarbaz)
    assert res == b"/foo/bar"
    assert get_tainted_ranges(res) == [TaintRange(0, 8, Source("test_ospath", b"/foo/bar/baz", OriginType.PARAMETER))]


def test_ospathdirname_bytes_nottainted():
    res = _aspect_ospathdirname(b"/foo/bar/baz")
    assert res == b"/foo/bar"
    assert not get_tainted_ranges(res)


def test_ospathdirname_single_slash_tainted():
    tainted_slash = taint_pyobject(
        pyobject="/",
        source_name="test_ospath",
        source_value="/",
        source_origin=OriginType.PARAMETER,
    )
    res = _aspect_ospathdirname(tainted_slash)
    assert res == "/"
    assert get_tainted_ranges(res) == [TaintRange(0, 1, Source("test_ospath", "/", OriginType.PARAMETER))]


def test_ospathdirname_nottainted_empty():
    res = _aspect_ospathdirname("")
    assert res == ""
    assert not get_tainted_ranges(res)