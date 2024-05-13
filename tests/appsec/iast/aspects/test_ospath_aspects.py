import os
import sys

import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import Source
from ddtrace.appsec._iast._taint_tracking import TaintRange
from ddtrace.appsec._iast._taint_tracking import _aspect_ospathbasename
from ddtrace.appsec._iast._taint_tracking import _aspect_ospathdirname
from ddtrace.appsec._iast._taint_tracking import _aspect_ospathjoin
from ddtrace.appsec._iast._taint_tracking import _aspect_ospathnormcase
from ddtrace.appsec._iast._taint_tracking import _aspect_ospathsplit
from ddtrace.appsec._iast._taint_tracking import _aspect_ospathsplitext


if sys.version_info >= (3, 12) or os.name == "nt":
    from ddtrace.appsec._iast._taint_tracking import _aspect_ospathsplitdrive
if sys.version_info >= (3, 12):
    from ddtrace.appsec._iast._taint_tracking import _aspect_ospathsplitroot
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


def test_ospathsplit_tainted_normal():
    tainted_foobarbaz = taint_pyobject(
        pyobject="/foo/bar/baz",
        source_name="test_ospath",
        source_value="/foo/bar/baz",
        source_origin=OriginType.PARAMETER,
    )

    res = _aspect_ospathsplit(tainted_foobarbaz)
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

    res = _aspect_ospathsplit(tainted_empty)
    assert res == ("", "")
    assert not get_tainted_ranges(res[0])
    assert not get_tainted_ranges(res[1])


def test_ospathsplit_nottainted():
    res = _aspect_ospathsplit("/foo/bar/baz")
    assert res == ("/foo/bar", "baz")
    assert not get_tainted_ranges(res[0])
    assert not get_tainted_ranges(res[1])


def test_ospathsplit_wrong_arg():
    with pytest.raises(TypeError):
        _ = _aspect_ospathsplit(42)


def test_ospathsplit_bytes_tainted():
    tainted_foobarbaz = taint_pyobject(
        pyobject=b"/foo/bar/baz",
        source_name="test_ospath",
        source_value=b"/foo/bar/baz",
        source_origin=OriginType.PARAMETER,
    )

    res = _aspect_ospathsplit(tainted_foobarbaz)
    assert res == (b"/foo/bar", b"baz")
    assert get_tainted_ranges(res[0]) == [
        TaintRange(0, 8, Source("test_ospath", b"/foo/bar/baz", OriginType.PARAMETER))
    ]
    assert get_tainted_ranges(res[1]) == [
        TaintRange(0, 3, Source("test_ospath", b"/foo/bar/baz", OriginType.PARAMETER))
    ]


def test_ospathsplit_bytes_nottainted():
    res = _aspect_ospathsplit(b"/foo/bar/baz")
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
    res = _aspect_ospathsplit(tainted_slash)
    assert res == ("/", "")
    assert get_tainted_ranges(res[0]) == [TaintRange(0, 1, Source("test_ospath", "/", OriginType.PARAMETER))]
    assert not get_tainted_ranges(res[1])


def test_ospathsplit_nottainted_empty():
    res = _aspect_ospathsplit("")
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

    res = _aspect_ospathsplitext(tainted_foobarbaz)
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

    res = _aspect_ospathsplitroot(tainted_foobarbaz)
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

    res = _aspect_ospathsplitroot(tainted_foobarbaz)
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

    res = _aspect_ospathsplitroot(tainted_foobarbaz)
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

    res = _aspect_ospathsplitroot(tainted_empty)
    assert res == ("", "", "")
    for i in res:
        assert not get_tainted_ranges(i)


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Requires Python 3.12")
def test_ospathsplitroot_nottainted():
    res = _aspect_ospathsplitroot("/foo/bar/baz")
    assert res == ("", "/", "foo/bar/baz")
    for i in res:
        assert not get_tainted_ranges(i)


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Requires Python 3.12")
def test_ospathsplitroot_wrong_arg():
    with pytest.raises(TypeError):
        _ = _aspect_ospathsplitroot(42)


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Requires Python 3.12")
def test_ospathsplitroot_bytes_tainted():
    tainted_foobarbaz = taint_pyobject(
        pyobject=b"/foo/bar/baz",
        source_name="test_ospath",
        source_value=b"/foo/bar/baz",
        source_origin=OriginType.PARAMETER,
    )

    res = _aspect_ospathsplitroot(tainted_foobarbaz)
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
    res = _aspect_ospathsplitroot(b"/foo/bar/baz")
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
    res = _aspect_ospathsplitroot(tainted_slash)
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

    res = _aspect_ospathsplitdrive(tainted_foobarbaz)
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

    res = _aspect_ospathsplitdrive(tainted_empty)
    assert res == ("", "")
    for i in res:
        assert not get_tainted_ranges(i)


# TODO: add tests for ospathsplitdrive with different drive letters that must run
# under Windows since they're noop under posix
