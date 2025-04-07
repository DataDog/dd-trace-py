import os
from pathlib import PosixPath
import sys

from hypothesis import given
from hypothesis.strategies import text
import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import Source
from ddtrace.appsec._iast._taint_tracking import TaintRange
from ddtrace.appsec._iast._taint_tracking.aspects import ospathbasename_aspect
from ddtrace.appsec._iast._taint_tracking.aspects import ospathdirname_aspect
from ddtrace.appsec._iast._taint_tracking.aspects import ospathjoin_aspect
from ddtrace.appsec._iast._taint_tracking.aspects import ospathnormcase_aspect
from ddtrace.appsec._iast._taint_tracking.aspects import ospathsplit_aspect
from ddtrace.appsec._iast._taint_tracking.aspects import ospathsplitext_aspect


if sys.version_info >= (3, 12) or os.name == "nt":
    from ddtrace.appsec._iast._taint_tracking.aspects import ospathsplitdrive_aspect
if sys.version_info >= (3, 12):
    from ddtrace.appsec._iast._taint_tracking.aspects import ospathsplitroot_aspect
from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject


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
    res = ospathjoin_aspect("root", tainted_foo, "nottainted", tainted_bar, "alsonottainted")
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

    res = ospathjoin_aspect("ignored", ignored_tainted_foo, "ignored_nottainted", tainted_slashbar, "alsonottainted")
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

    res = ospathjoin_aspect(tainted_foo, "nottainted", tainted_bar, "alsonottainted")
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

    res = ospathjoin_aspect(tainted_slashfoo, "nottainted", tainted_bar, "alsonottainted")
    assert res == "/foo/nottainted/bar/alsonottainted"
    assert get_tainted_ranges(res) == [
        TaintRange(0, 4, Source("test_ospath", "/foo", OriginType.PARAMETER)),
        TaintRange(16, 3, Source("test_ospath", "bar", OriginType.PARAMETER)),
    ]


def test_ospathjoin_single_arg_nottainted():
    res = ospathjoin_aspect("nottainted")
    assert res == "nottainted"
    assert not get_tainted_ranges(res)

    res = ospathjoin_aspect("/nottainted")
    assert res == "/nottainted"
    assert not get_tainted_ranges(res)


def test_ospathjoin_single_arg_tainted():
    tainted_foo = taint_pyobject(
        pyobject="foo",
        source_name="test_ospath",
        source_value="foo",
        source_origin=OriginType.PARAMETER,
    )
    res = ospathjoin_aspect(tainted_foo)
    assert res == "foo"
    assert get_tainted_ranges(res) == [TaintRange(0, 3, Source("test_ospath", "/foo", OriginType.PARAMETER))]

    tainted_slashfoo = taint_pyobject(
        pyobject="/foo",
        source_name="test_ospath",
        source_value="/foo",
        source_origin=OriginType.PARAMETER,
    )
    res = ospathjoin_aspect(tainted_slashfoo)
    assert res == "/foo"
    assert get_tainted_ranges(res) == [TaintRange(0, 4, Source("test_ospath", "/foo", OriginType.PARAMETER))]


def test_ospathjoin_last_slash_nottainted():
    tainted_foo = taint_pyobject(
        pyobject="foo",
        source_name="test_ospath",
        source_value="foo",
        source_origin=OriginType.PARAMETER,
    )

    res = ospathjoin_aspect("root", tainted_foo, "/nottainted")
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
    res = ospathjoin_aspect("root", tainted_foo, "nottainted", tainted_slashbar)
    assert res == "/bar"
    assert get_tainted_ranges(res) == [TaintRange(0, 4, Source("test_ospath", "/bar", OriginType.PARAMETER))]


def test_ospathjoin_wrong_arg():
    with pytest.raises(TypeError):
        _ = os.path.join("root", 42, "foobar")

    with pytest.raises(TypeError):
        _ = os.path.join(("a", "b"))

    with pytest.raises(TypeError):
        _ = ospathjoin_aspect("root", 42, "foobar")

    with pytest.raises(TypeError):
        _ = ospathjoin_aspect(("a", "b"))


@given(text())
def test_ospathbasename_no_exceptions(string_):
    assert os.path.basename(PosixPath(string_)) == ospathbasename_aspect(PosixPath(string_))
    assert os.path.basename(string_) == ospathbasename_aspect(string_)


@given(text())
def test_ospathdirname_no_exceptions(string_):
    assert os.path.dirname(PosixPath(string_)) == ospathdirname_aspect(PosixPath(string_))
    assert os.path.dirname(string_) == ospathdirname_aspect(string_)


@given(text())
def test_ospathjoin_no_exceptions(string_):
    assert os.path.join(PosixPath(string_), string_) == ospathjoin_aspect(PosixPath(string_), string_)


@given(text())
def test_ospathnormcase_no_exceptions(string_):
    assert os.path.normcase(PosixPath(string_)) == ospathnormcase_aspect(PosixPath(string_))
    assert os.path.normcase(string_) == ospathnormcase_aspect(string_)


@given(text())
def test_ospathsplit_no_exceptions(string_):
    assert os.path.split(PosixPath(string_)) == ospathsplit_aspect(PosixPath(string_))
    assert os.path.split(string_) == ospathsplit_aspect(string_)


@given(text())
def test_ospathsplitdrive_no_exceptions(string_):
    assert os.path.splitdrive(PosixPath(string_)) == ospathsplitdrive_aspect(PosixPath(string_))
    assert os.path.splitdrive(string_) == ospathsplitdrive_aspect(string_)


@given(text())
def test_ospathsplitext_no_exceptions(string_):
    assert os.path.splitext(PosixPath(string_)) == ospathsplitext_aspect(PosixPath(string_))
    assert os.path.splitext(string_) == ospathsplitext_aspect(string_)


@given(text())
def test_ospathsplitroot_no_exceptions(string_):
    assert os.path.splitroot(PosixPath(string_)) == ospathsplitroot_aspect(PosixPath(string_))
    assert os.path.splitroot(string_) == ospathsplitroot_aspect(string_)


def test_ospathjoin_bytes_nottainted():
    res = ospathjoin_aspect(b"nottainted", b"alsonottainted")
    assert res == b"nottainted/alsonottainted"


def test_ospathjoin_bytes_tainted():
    tainted_foo = taint_pyobject(
        pyobject=b"foo",
        source_name="test_ospath",
        source_value=b"foo",
        source_origin=OriginType.PARAMETER,
    )
    res = ospathjoin_aspect(tainted_foo, b"nottainted")
    assert res == b"foo/nottainted"
    assert get_tainted_ranges(res) == [TaintRange(0, 3, Source("test_ospath", b"foo", OriginType.PARAMETER))]

    tainted_slashfoo = taint_pyobject(
        pyobject=b"/foo",
        source_name="test_ospath",
        source_value=b"/foo",
        source_origin=OriginType.PARAMETER,
    )

    res = ospathjoin_aspect(tainted_slashfoo, b"nottainted")
    assert res == b"/foo/nottainted"
    assert get_tainted_ranges(res) == [TaintRange(0, 4, Source("test_ospath", b"/foo", OriginType.PARAMETER))]

    res = ospathjoin_aspect(b"nottainted_ignore", b"alsoignored", tainted_slashfoo)
    assert res == b"/foo"
    assert get_tainted_ranges(res) == [TaintRange(0, 4, Source("test_ospath", b"/foo", OriginType.PARAMETER))]


def test_ospathjoin_empty():
    res = ospathjoin_aspect("")
    assert res == ""


def test_ospathjoin_noparams():
    with pytest.raises(TypeError):
        _ = ospathjoin_aspect()


def test_ospathbasename_tainted_normal():
    tainted_foobarbaz = taint_pyobject(
        pyobject="/foo/bar/baz",
        source_name="test_ospath",
        source_value="/foo/bar/baz",
        source_origin=OriginType.PARAMETER,
    )

    res = ospathbasename_aspect(tainted_foobarbaz)
    assert res == "baz"
    assert get_tainted_ranges(res) == [TaintRange(0, 3, Source("test_ospath", "/foo/bar/baz", OriginType.PARAMETER))]


def test_ospathbasename_tainted_empty():
    tainted_empty = taint_pyobject(
        pyobject="",
        source_name="test_ospath",
        source_value="",
        source_origin=OriginType.PARAMETER,
    )

    res = ospathbasename_aspect(tainted_empty)
    assert res == ""
    assert not get_tainted_ranges(res)


def test_ospathbasename_nottainted():
    res = ospathbasename_aspect("/foo/bar/baz")
    assert res == "baz"
    assert not get_tainted_ranges(res)


def test_ospathbasename_wrong_arg():
    with pytest.raises(TypeError):
        _ = ospathbasename_aspect(42)


def test_ospathbasename_bytes_tainted():
    tainted_foobarbaz = taint_pyobject(
        pyobject=b"/foo/bar/baz",
        source_name="test_ospath",
        source_value=b"/foo/bar/baz",
        source_origin=OriginType.PARAMETER,
    )

    res = ospathbasename_aspect(tainted_foobarbaz)
    assert res == b"baz"
    assert get_tainted_ranges(res) == [TaintRange(0, 3, Source("test_ospath", b"/foo/bar/baz", OriginType.PARAMETER))]


def test_ospathbasename_bytes_nottainted():
    res = ospathbasename_aspect(b"/foo/bar/baz")
    assert res == b"baz"
    assert not get_tainted_ranges(res)


def test_ospathbasename_single_slash_tainted():
    tainted_slash = taint_pyobject(
        pyobject="/",
        source_name="test_ospath",
        source_value="/",
        source_origin=OriginType.PARAMETER,
    )
    res = ospathbasename_aspect(tainted_slash)
    assert res == ""
    assert not get_tainted_ranges(res)


def test_ospathbasename_nottainted_empty():
    res = ospathbasename_aspect("")
    assert res == ""
    assert not get_tainted_ranges(res)


def test_ospathnormcase_tainted_normal():
    tainted_foobarbaz = taint_pyobject(
        pyobject="/foo/bar/baz",
        source_name="test_ospath",
        source_value="/foo/bar/baz",
        source_origin=OriginType.PARAMETER,
    )

    res = ospathnormcase_aspect(tainted_foobarbaz)
    assert res == "/foo/bar/baz"
    assert get_tainted_ranges(res) == [TaintRange(0, 12, Source("test_ospath", "/foo/bar/baz", OriginType.PARAMETER))]


def test_ospathnormcase_tainted_empty():
    tainted_empty = taint_pyobject(
        pyobject="",
        source_name="test_ospath",
        source_value="",
        source_origin=OriginType.PARAMETER,
    )

    res = ospathnormcase_aspect(tainted_empty)
    assert res == ""
    assert not get_tainted_ranges(res)


def test_ospathnormcase_nottainted():
    res = ospathnormcase_aspect("/foo/bar/baz")
    assert res == "/foo/bar/baz"
    assert not get_tainted_ranges(res)


def test_ospathnormcase_wrong_arg():
    with pytest.raises(TypeError):
        _ = ospathnormcase_aspect(42)


def test_ospathnormcase_bytes_tainted():
    tainted_foobarbaz = taint_pyobject(
        pyobject=b"/foo/bar/baz",
        source_name="test_ospath",
        source_value=b"/foo/bar/baz",
        source_origin=OriginType.PARAMETER,
    )

    res = ospathnormcase_aspect(tainted_foobarbaz)
    assert res == b"/foo/bar/baz"
    assert get_tainted_ranges(res) == [TaintRange(0, 12, Source("test_ospath", b"/foo/bar/baz", OriginType.PARAMETER))]


def test_ospathnormcase_bytes_nottainted():
    res = ospathnormcase_aspect(b"/foo/bar/baz")
    assert res == b"/foo/bar/baz"
    assert not get_tainted_ranges(res)


def test_ospathnormcase_single_slash_tainted():
    tainted_slash = taint_pyobject(
        pyobject="/",
        source_name="test_ospath",
        source_value="/",
        source_origin=OriginType.PARAMETER,
    )
    res = ospathnormcase_aspect(tainted_slash)
    assert res == "/"
    assert get_tainted_ranges(res) == [TaintRange(0, 1, Source("test_ospath", "/", OriginType.PARAMETER))]


def test_ospathnormcase_nottainted_empty():
    res = ospathnormcase_aspect("")
    assert res == ""
    assert not get_tainted_ranges(res)


def test_ospathdirname_tainted_normal():
    tainted_foobarbaz = taint_pyobject(
        pyobject="/foo/bar/baz",
        source_name="test_ospath",
        source_value="/foo/bar/baz",
        source_origin=OriginType.PARAMETER,
    )

    res = ospathdirname_aspect(tainted_foobarbaz)
    assert res == "/foo/bar"
    assert get_tainted_ranges(res) == [TaintRange(0, 8, Source("test_ospath", "/foo/bar/baz", OriginType.PARAMETER))]


def test_ospathdirname_tainted_empty():
    tainted_empty = taint_pyobject(
        pyobject="",
        source_name="test_ospath",
        source_value="",
        source_origin=OriginType.PARAMETER,
    )

    res = ospathdirname_aspect(tainted_empty)
    assert res == ""
    assert not get_tainted_ranges(res)


def test_ospathdirname_nottainted():
    res = ospathdirname_aspect("/foo/bar/baz")
    assert res == "/foo/bar"
    assert not get_tainted_ranges(res)


def test_ospathdirname_wrong_arg():
    with pytest.raises(TypeError):
        _ = ospathdirname_aspect(42)


def test_ospathdirname_bytes_tainted():
    tainted_foobarbaz = taint_pyobject(
        pyobject=b"/foo/bar/baz",
        source_name="test_ospath",
        source_value=b"/foo/bar/baz",
        source_origin=OriginType.PARAMETER,
    )

    res = ospathdirname_aspect(tainted_foobarbaz)
    assert res == b"/foo/bar"
    assert get_tainted_ranges(res) == [TaintRange(0, 8, Source("test_ospath", b"/foo/bar/baz", OriginType.PARAMETER))]


def test_ospathdirname_bytes_nottainted():
    res = ospathdirname_aspect(b"/foo/bar/baz")
    assert res == b"/foo/bar"
    assert not get_tainted_ranges(res)


def test_ospathdirname_single_slash_tainted():
    tainted_slash = taint_pyobject(
        pyobject="/",
        source_name="test_ospath",
        source_value="/",
        source_origin=OriginType.PARAMETER,
    )
    res = ospathdirname_aspect(tainted_slash)
    assert res == "/"
    assert get_tainted_ranges(res) == [TaintRange(0, 1, Source("test_ospath", "/", OriginType.PARAMETER))]


def test_ospathdirname_nottainted_empty():
    res = ospathdirname_aspect("")
    assert res == ""
    assert not get_tainted_ranges(res)


def test_ospathsplit_tainted_normal():
    tainted_foobarbaz = taint_pyobject(
        pyobject="/foo/bar/baz",
        source_name="test_ospath",
        source_value="/foo/bar/baz",
        source_origin=OriginType.PARAMETER,
    )

    res = ospathsplit_aspect(tainted_foobarbaz)
    assert res == ("/foo/bar", "baz")
    assert get_tainted_ranges(res[0]) == [TaintRange(0, 8, Source("test_ospath", "/foo/bar/baz", OriginType.PARAMETER))]
    assert get_tainted_ranges(res[1]) == [TaintRange(0, 3, Source("test_ospath", "/foo/bar/baz", OriginType.PARAMETER))]


def test_ospathsplit_tainted_empty():
    tainted_empty = taint_pyobject(
        pyobject="",
        source_name="test_ospath",
        source_value="",
        source_origin=OriginType.PARAMETER,
    )

    res = ospathsplit_aspect(tainted_empty)
    assert res == ("", "")
    assert not get_tainted_ranges(res[0])
    assert not get_tainted_ranges(res[1])


def test_ospathsplit_nottainted():
    res = ospathsplit_aspect("/foo/bar/baz")
    assert res == ("/foo/bar", "baz")
    assert not get_tainted_ranges(res[0])
    assert not get_tainted_ranges(res[1])


def test_ospathsplit_wrong_arg():
    with pytest.raises(TypeError):
        _ = ospathsplit_aspect(42)


def test_ospathsplit_bytes_tainted():
    tainted_foobarbaz = taint_pyobject(
        pyobject=b"/foo/bar/baz",
        source_name="test_ospath",
        source_value=b"/foo/bar/baz",
        source_origin=OriginType.PARAMETER,
    )

    res = ospathsplit_aspect(tainted_foobarbaz)
    assert res == (b"/foo/bar", b"baz")
    assert get_tainted_ranges(res[0]) == [
        TaintRange(0, 8, Source("test_ospath", b"/foo/bar/baz", OriginType.PARAMETER))
    ]
    assert get_tainted_ranges(res[1]) == [
        TaintRange(0, 3, Source("test_ospath", b"/foo/bar/baz", OriginType.PARAMETER))
    ]


def test_ospathsplit_bytes_nottainted():
    res = ospathsplit_aspect(b"/foo/bar/baz")
    assert res == (b"/foo/bar", b"baz")
    assert not get_tainted_ranges(res[0])
    assert not get_tainted_ranges(res[1])


def test_ospathsplit_single_slash_tainted():
    tainted_slash = taint_pyobject(
        pyobject="/",
        source_name="test_ospath",
        source_value="/",
        source_origin=OriginType.PARAMETER,
    )
    res = ospathsplit_aspect(tainted_slash)
    assert res == ("/", "")
    assert get_tainted_ranges(res[0]) == [TaintRange(0, 1, Source("test_ospath", "/", OriginType.PARAMETER))]
    assert not get_tainted_ranges(res[1])


def test_ospathsplit_nottainted_empty():
    res = ospathsplit_aspect("")
    assert res == ("", "")
    assert not get_tainted_ranges(res[0])
    assert not get_tainted_ranges(res[1])


def test_ospathsplitext_tainted_normal():
    tainted_foobarbaz = taint_pyobject(
        pyobject="/foo/bar/baz.jpg",
        source_name="test_ospath",
        source_value="/foo/bar/baz.jpg",
        source_origin=OriginType.PARAMETER,
    )

    res = ospathsplitext_aspect(tainted_foobarbaz)
    assert res == ("/foo/bar/baz", ".jpg")
    assert get_tainted_ranges(res[0]) == [
        TaintRange(0, 12, Source("test_ospath", "/foo/bar/baz.jpg", OriginType.PARAMETER))
    ]
    assert get_tainted_ranges(res[1]) == [
        TaintRange(0, 4, Source("test_ospath", "/foo/bar/baz.jpg", OriginType.PARAMETER))
    ]


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Requires Python 3.12")
def test_ospathsplitroot_tainted_normal():
    tainted_foobarbaz = taint_pyobject(
        pyobject="/foo/bar/baz",
        source_name="test_ospath",
        source_value="/foo/bar/baz",
        source_origin=OriginType.PARAMETER,
    )

    res = ospathsplitroot_aspect(tainted_foobarbaz)
    assert res == ("", "/", "foo/bar/baz")
    assert not get_tainted_ranges(res[0])
    assert get_tainted_ranges(res[1]) == [TaintRange(0, 1, Source("test_ospath", "/foo/bar/baz", OriginType.PARAMETER))]
    assert get_tainted_ranges(res[2]) == [
        TaintRange(0, 11, Source("test_ospath", "/foo/bar/baz", OriginType.PARAMETER))
    ]


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Requires Python 3.12")
def test_ospathsplitroot_tainted_doble_initial_slash():
    tainted_foobarbaz = taint_pyobject(
        pyobject="//foo/bar/baz",
        source_name="test_ospath",
        source_value="//foo/bar/baz",
        source_origin=OriginType.PARAMETER,
    )

    res = ospathsplitroot_aspect(tainted_foobarbaz)
    assert res == ("", "//", "foo/bar/baz")
    assert not get_tainted_ranges(res[0])
    assert get_tainted_ranges(res[1]) == [
        TaintRange(0, 2, Source("test_ospath", "//foo/bar/baz", OriginType.PARAMETER))
    ]
    assert get_tainted_ranges(res[2]) == [
        TaintRange(0, 11, Source("test_ospath", "//foo/bar/baz", OriginType.PARAMETER))
    ]


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Requires Python 3.12")
def test_ospathsplitroot_tainted_triple_initial_slash():
    tainted_foobarbaz = taint_pyobject(
        pyobject="///foo/bar/baz",
        source_name="test_ospath",
        source_value="///foo/bar/baz",
        source_origin=OriginType.PARAMETER,
    )

    res = ospathsplitroot_aspect(tainted_foobarbaz)
    assert res == ("", "/", "//foo/bar/baz")
    assert not get_tainted_ranges(res[0])
    assert get_tainted_ranges(res[1]) == [
        TaintRange(0, 1, Source("test_ospath", "///foo/bar/baz", OriginType.PARAMETER))
    ]
    assert get_tainted_ranges(res[2]) == [
        TaintRange(0, 13, Source("test_ospath", "///foo/bar/baz", OriginType.PARAMETER))
    ]


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Requires Python 3.12")
def test_ospathsplitroot_tainted_empty():
    tainted_empty = taint_pyobject(
        pyobject="",
        source_name="test_ospath",
        source_value="",
        source_origin=OriginType.PARAMETER,
    )

    res = ospathsplitroot_aspect(tainted_empty)
    assert res == ("", "", "")
    for i in res:
        assert not get_tainted_ranges(i)


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Requires Python 3.12")
def test_ospathsplitroot_nottainted():
    res = ospathsplitroot_aspect("/foo/bar/baz")
    assert res == ("", "/", "foo/bar/baz")
    for i in res:
        assert not get_tainted_ranges(i)


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Requires Python 3.12")
def test_ospathsplitroot_wrong_arg():
    with pytest.raises(TypeError):
        _ = ospathsplitroot_aspect(42)


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Requires Python 3.12")
def test_ospathsplitroot_bytes_tainted():
    tainted_foobarbaz = taint_pyobject(
        pyobject=b"/foo/bar/baz",
        source_name="test_ospath",
        source_value=b"/foo/bar/baz",
        source_origin=OriginType.PARAMETER,
    )

    res = ospathsplitroot_aspect(tainted_foobarbaz)
    assert res == (b"", b"/", b"foo/bar/baz")
    assert not get_tainted_ranges(res[0])
    assert get_tainted_ranges(res[1]) == [
        TaintRange(0, 1, Source("test_ospath", b"/foo/bar/baz", OriginType.PARAMETER))
    ]
    assert get_tainted_ranges(res[2]) == [
        TaintRange(0, 11, Source("test_ospath", b"/foo/bar/baz", OriginType.PARAMETER))
    ]


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Requires Python 3.12")
def test_ospathsplitroot_bytes_nottainted():
    res = ospathsplitroot_aspect(b"/foo/bar/baz")
    assert res == (b"", b"/", b"foo/bar/baz")
    for i in res:
        assert not get_tainted_ranges(i)


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Requires Python 3.12")
def tets_ospathsplitroot_single_slash_tainted():
    tainted_slash = taint_pyobject(
        pyobject="/",
        source_name="test_ospath",
        source_value="/",
        source_origin=OriginType.PARAMETER,
    )
    res = ospathsplitroot_aspect(tainted_slash)
    assert res == ("", "/", "")
    assert not get_tainted_ranges(res[0])
    assert get_tainted_ranges(res[1]) == [TaintRange(0, 1, Source("test_ospath", "/", OriginType.PARAMETER))]
    assert not get_tainted_ranges(res[2])


@pytest.mark.skipif(sys.version_info < (3, 12) and os.name != "nt", reason="Requires Python 3.12 or Windows")
def test_ospathsplitdrive_tainted_normal():
    tainted_foobarbaz = taint_pyobject(
        pyobject="/foo/bar/baz",
        source_name="test_ospath",
        source_value="/foo/bar/baz",
        source_origin=OriginType.PARAMETER,
    )

    res = ospathsplitdrive_aspect(tainted_foobarbaz)
    assert res == ("", "/foo/bar/baz")
    assert not get_tainted_ranges(res[0])
    assert get_tainted_ranges(res[1]) == [
        TaintRange(0, 12, Source("test_ospath", "/foo/bar/baz", OriginType.PARAMETER))
    ]


@pytest.mark.skipif(sys.version_info < (3, 12) and os.name != "nt", reason="Requires Python 3.12 or Windows")
def test_ospathsplitdrive_tainted_empty():
    tainted_empty = taint_pyobject(
        pyobject="",
        source_name="test_ospath",
        source_value="",
        source_origin=OriginType.PARAMETER,
    )

    res = ospathsplitdrive_aspect(tainted_empty)
    assert res == ("", "")
    for i in res:
        assert not get_tainted_ranges(i)


# TODO: add tests for ospathsplitdrive with different drive letters that must run
# under Windows since they're noop under posix
