import os

import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import Source
from ddtrace.appsec._iast._taint_tracking import TaintRange
from ddtrace.appsec._iast._taint_tracking import as_formatted_evidence
from ddtrace.appsec._iast._taint_tracking import common_replace
from ddtrace.appsec._iast._taint_tracking import get_ranges
from ddtrace.appsec._iast._taint_tracking import set_ranges
from ddtrace.appsec._iast._taint_tracking import set_ranges_on_splitted
from ddtrace.appsec._iast._taint_tracking.aspects import _convert_escaped_text_to_tainted_text


_SOURCE1 = Source(name="name", value="value", origin=OriginType.COOKIE)
_SOURCE2 = Source(name="name2", value="value2", origin=OriginType.BODY)

_RANGE1 = TaintRange(0, 2, _SOURCE1)
_RANGE2 = TaintRange(1, 3, _SOURCE2)


def test_common_replace_untainted():
    s = "foobar"
    assert common_replace("upper", s) == "FOOBAR"


def test_common_replace_untainted_missing_method():
    s = "foobar"
    with pytest.raises(AttributeError):
        common_replace("doesntexist", s)

    with pytest.raises(AttributeError):
        common_replace("", s)


def test_common_replace_untainted_args_method():
    s = "foobar"
    # with pytest.raises(AttributeError):
    res = common_replace("rjust", s, 10, " ")
    assert res == "    foobar"


def test_common_replace_untainted_wrong_args_method():
    s = "foobar"
    # with pytest.raises(AttributeError):
    with pytest.raises(TypeError):
        _ = common_replace("rjust", s, "10", 35)


def test_common_replace_tainted_str():
    s = "FooBar"
    set_ranges(s, [_RANGE1, _RANGE2])
    s2 = common_replace("lower", s)
    assert s2 == "foobar"
    assert get_ranges(s2) == [_RANGE1, _RANGE2]


def test_common_replace_tainted_bytes():
    s = b"FooBar"
    set_ranges(s, [_RANGE1, _RANGE2])
    s2 = common_replace("lower", s)
    assert s2 == b"foobar"
    assert get_ranges(s2) == [_RANGE1, _RANGE2]


def test_common_replace_tainted_bytearray():
    s = b"FooBar"
    set_ranges(s, [_RANGE1, _RANGE2])
    s2 = common_replace("lower", s)
    assert s2 == b"foobar"
    assert get_ranges(s2) == [_RANGE1, _RANGE2]


def _build_sample_range(start, length, name):  # type: (int, int) -> TaintRange
    return TaintRange(start, length, Source(name, "sample_value", OriginType.PARAMETER))


def test_as_formatted_evidence():  # type: () -> None
    s = "abcdefgh"
    set_ranges(s, (_build_sample_range(0, 5, "first"),))
    assert as_formatted_evidence(s) == ":+-<first>abcde<first>-+:fgh"

    set_ranges(s, (_build_sample_range(1, 6, "first"),))
    assert as_formatted_evidence(s) == "a:+-<first>bcdefg<first>-+:h"

    set_ranges(
        s,
        (
            _build_sample_range(0, 2, "first"),
            _build_sample_range(3, 1, "second"),
            _build_sample_range(4, 2, "third"),
        ),
    )
    assert as_formatted_evidence(s) == ":+-<first>ab<first>-+:c:+-<second>d<second>-+::+-<third>ef<third>-+:gh"

    set_ranges(s, (_build_sample_range(3, 2, "second"), _build_sample_range(0, 2, "first")))
    assert as_formatted_evidence(s) == ":+-<first>ab<first>-+:c:+-<second>de<second>-+:fgh"


def test_as_formatted_evidence_convert_escaped_text_to_tainted_text():  # type: () -> None
    from ddtrace.appsec._iast._taint_tracking import TagMappingMode

    s = "abcdefgh"
    ranges = _build_sample_range(0, 5, "2")
    set_ranges(s, (ranges,))
    assert (
        as_formatted_evidence(s, tag_mapping_function=TagMappingMode.Mapper) == ":+-<1750328947>abcde<1750328947>-+:fgh"
    )
    assert _convert_escaped_text_to_tainted_text(":+-<1750328947>abcde<1750328947>-+:fgh", [ranges]) == "abcdefgh"


def test_set_ranges_on_splitted_str() -> None:
    s = "abc|efgh"
    range1 = _build_sample_range(0, 2, "first")
    range2 = _build_sample_range(4, 2, "second")
    set_ranges(s, (range1, range2))
    ranges = get_ranges(s)
    assert ranges

    parts = s.split("|")
    assert set_ranges_on_splitted(s, ranges, parts)
    assert get_ranges(parts[0]) == [TaintRange(0, 2, Source("first", "sample_value", OriginType.PARAMETER))]
    assert get_ranges(parts[1]) == [TaintRange(0, 2, Source("second", "sample_value", OriginType.PARAMETER))]


def test_set_ranges_on_splitted_rsplit() -> None:
    s = "abc|efgh|jkl"
    range1 = _build_sample_range(0, 2, s[0:2])
    range2 = _build_sample_range(4, 2, s[4:6])
    range3 = _build_sample_range(9, 3, s[9:12])
    set_ranges(s, (range1, range2, range3))
    ranges = get_ranges(s)
    assert ranges

    parts = s.rsplit("|", 1)
    assert parts == ["abc|efgh", "jkl"]
    assert set_ranges_on_splitted(s, ranges, parts)
    assert get_ranges(parts[0]) == [
        TaintRange(0, 2, Source("ab", "sample_value", OriginType.PARAMETER)),
        TaintRange(4, 2, Source("ef", "sample_value", OriginType.PARAMETER)),
    ]
    assert get_ranges(parts[1]) == [
        TaintRange(0, 3, Source("jkl", "sample_value", OriginType.PARAMETER)),
    ]


def test_set_ranges_on_splitted_ospathsplit():
    s = "abc/efgh/jkl"
    range1 = _build_sample_range(0, 4, s[0:4])
    range2 = _build_sample_range(4, 4, s[4:8])
    range3 = _build_sample_range(9, 3, s[9:12])
    set_ranges(s, (range1, range2, range3))
    ranges = get_ranges(s)
    assert ranges

    parts = list(os.path.split(s))
    assert parts == ["abc/efgh", "jkl"]
    assert set_ranges_on_splitted(s, ranges, parts)
    assert get_ranges(parts[0]) == [
        TaintRange(0, 4, Source("abc/", "sample_value", OriginType.PARAMETER)),
        TaintRange(4, 4, Source("efgh", "sample_value", OriginType.PARAMETER)),
    ]
    assert get_ranges(parts[1]) == [
        TaintRange(0, 3, Source("jkl", "sample_value", OriginType.PARAMETER)),
    ]


def test_set_ranges_on_splitted_ospathsplitext():
    s = "abc/efgh/jkl.txt"
    range1 = _build_sample_range(0, 3, s[0:2])
    range2 = _build_sample_range(4, 4, s[4:8])
    range3 = _build_sample_range(9, 3, s[9:12])
    range4 = _build_sample_range(13, 4, s[13:17])
    set_ranges(s, (range1, range2, range3, range4))
    ranges = get_ranges(s)
    assert ranges

    parts = list(os.path.splitext(s))
    assert parts == ["abc/efgh/jkl", ".txt"]
    assert set_ranges_on_splitted(s, ranges, parts, include_separator=True)
    assert get_ranges(parts[0]) == [
        TaintRange(0, 3, Source("abc", "sample_value", OriginType.PARAMETER)),
        TaintRange(4, 4, Source("efgh", "sample_value", OriginType.PARAMETER)),
        TaintRange(9, 3, Source("jkl", "sample_value", OriginType.PARAMETER)),
    ]
    assert get_ranges(parts[1]) == [
        TaintRange(1, 4, Source("txt", "sample_value", OriginType.PARAMETER)),
    ]


def test_set_ranges_on_splitted_ospathsplit_with_empty_string():
    s = "abc/efgh/jkl/"
    range1 = _build_sample_range(0, 2, s[0:2])
    range2 = _build_sample_range(4, 4, s[4:8])
    range3 = _build_sample_range(9, 3, s[9:12])
    set_ranges(s, (range1, range2, range3))
    ranges = get_ranges(s)
    assert ranges

    parts = list(os.path.split(s))
    assert parts == ["abc/efgh/jkl", ""]
    assert set_ranges_on_splitted(s, ranges, parts)
    assert get_ranges(parts[0]) == [
        TaintRange(0, 2, Source("ab", "sample_value", OriginType.PARAMETER)),
        TaintRange(4, 4, Source("efgh", "sample_value", OriginType.PARAMETER)),
        TaintRange(9, 3, Source("jkl", "sample_value", OriginType.PARAMETER)),
    ]
    assert get_ranges(parts[1]) == []


def test_set_ranges_on_splitted_ospathbasename():
    s = "abc/efgh/jkl"
    range1 = _build_sample_range(0, 2, s[0:2])
    range2 = _build_sample_range(4, 4, s[4:8])
    range3 = _build_sample_range(9, 3, s[9:12])
    set_ranges(s, (range1, range2, range3))
    ranges = get_ranges(s)
    assert ranges

    # Basename aspect implementation works by adding the previous content in a list so
    # we can use set_ranges_on_splitted to set the ranges on the last part (the real result)
    parts = ["abc/efgh/", os.path.basename(s)]
    assert parts == ["abc/efgh/", "jkl"]
    assert set_ranges_on_splitted(s, ranges, parts, include_separator=True)
    assert get_ranges(parts[1]) == [
        TaintRange(0, 3, Source("jkl", "sample_value", OriginType.PARAMETER)),
    ]


def test_set_ranges_on_splitted_ospathsplitdrive_windows():
    s = "C:/abc/efgh/jkl"
    range1 = _build_sample_range(0, 2, s[0:2])
    range2 = _build_sample_range(4, 4, s[4:8])
    range3 = _build_sample_range(9, 3, s[9:12])
    range4 = _build_sample_range(12, 3, s[12:16])
    set_ranges(s, (range1, range2, range3, range4))
    ranges = get_ranges(s)
    assert ranges

    # We emulate what os.path.splitdrive would do on Windows instead of calling it
    parts = ["C:", "/abc/efgh/jkl"]
    assert set_ranges_on_splitted(s, ranges, parts, include_separator=True)
    assert get_ranges(parts[0]) == [
        TaintRange(0, 2, Source("C:", "sample_value", OriginType.PARAMETER)),
    ]
    assert get_ranges(parts[1]) == [
        TaintRange(2, 4, Source("bc/e", "sample_value", OriginType.PARAMETER)),
        TaintRange(7, 3, Source("gh/", "sample_value", OriginType.PARAMETER)),
        TaintRange(10, 3, Source("jkl", "sample_value", OriginType.PARAMETER)),
    ]


def test_set_ranges_on_splitted_ospathsplitdrive_posix():
    s = "/abc/efgh/jkl"
    range1 = _build_sample_range(0, 2, s[0:2])
    range2 = _build_sample_range(4, 4, s[4:8])
    range3 = _build_sample_range(9, 3, s[9:12])
    set_ranges(s, (range1, range2, range3))
    ranges = get_ranges(s)
    assert ranges

    # We emulate what os.path.splitdrive would do on posix instead of calling it
    parts = ["", "/abc/efgh/jkl"]
    assert set_ranges_on_splitted(s, ranges, parts)
    assert get_ranges(parts[0]) == []
    assert get_ranges(parts[1]) == ranges


def test_set_ranges_on_splitted_ospathsplitroot_windows_drive():
    s = "C:/abc/efgh/jkl"
    range1 = _build_sample_range(0, 2, s[0:2])
    range2 = _build_sample_range(4, 4, s[4:8])
    range3 = _build_sample_range(9, 3, s[9:12])
    range4 = _build_sample_range(12, 3, s[12:16])
    set_ranges(s, (range1, range2, range3, range4))
    ranges = get_ranges(s)
    assert ranges

    # We emulate what os.path.splitroot would do on Windows instead of calling it
    parts = ["C:", "/", "abc/efgh/jkl"]
    assert set_ranges_on_splitted(s, ranges, parts, include_separator=True)
    assert get_ranges(parts[0]) == [
        TaintRange(0, 2, Source("C:", "sample_value", OriginType.PARAMETER)),
    ]
    assert get_ranges(parts[1]) == []
    assert get_ranges(parts[2]) == [
        TaintRange(1, 4, Source("bc/e", "sample_value", OriginType.PARAMETER)),
        TaintRange(6, 3, Source("gh/", "sample_value", OriginType.PARAMETER)),
        TaintRange(9, 3, Source("jkl", "sample_value", OriginType.PARAMETER)),
    ]


def test_set_ranges_on_splitted_ospathsplitroot_windows_share():
    s = "//server/share/abc/efgh/jkl"
    range1 = _build_sample_range(0, 2, "//")
    range2 = _build_sample_range(2, 6, "server")
    range3 = _build_sample_range(9, 5, "share")
    range4 = _build_sample_range(14, 1, "/")
    range5 = _build_sample_range(15, 3, "abc")
    range6 = _build_sample_range(19, 4, "efgh")
    range7 = _build_sample_range(23, 4, "/jkl")
    set_ranges(s, (range1, range2, range3, range4, range5, range6, range7))
    ranges = get_ranges(s)
    assert ranges

    # We emulate what os.path.splitroot would do on Windows instead of calling it; the implementation
    # removed the second element
    parts = ["//server/share", "/", "abc/efgh/jkl"]
    assert set_ranges_on_splitted(s, ranges, parts, include_separator=True)
    assert get_ranges(parts[0]) == [
        TaintRange(0, 2, Source("//", "sample_value", OriginType.PARAMETER)),
        TaintRange(2, 6, Source("server", "sample_value", OriginType.PARAMETER)),
        TaintRange(9, 5, Source("share", "sample_value", OriginType.PARAMETER)),
    ]
    assert get_ranges(parts[1]) == [
        TaintRange(0, 1, Source("/", "sample_value", OriginType.PARAMETER)),
    ]
    assert get_ranges(parts[2]) == [
        TaintRange(0, 3, Source("abc", "sample_value", OriginType.PARAMETER)),
        TaintRange(4, 4, Source("efgh", "sample_value", OriginType.PARAMETER)),
        TaintRange(8, 4, Source("/jkl", "sample_value", OriginType.PARAMETER)),
    ]


def test_set_ranges_on_splitted_ospathsplitroot_posix_normal_path():
    s = "/abc/efgh/jkl"
    range1 = _build_sample_range(0, 4, "/abc")
    range2 = _build_sample_range(3, 5, "c/efg")
    range3 = _build_sample_range(7, 5, "gh/jk")
    set_ranges(s, (range1, range2, range3))
    ranges = get_ranges(s)
    assert ranges

    # We emulate what os.path.splitroot would do on posix instead of calling it
    parts = ["", "/", "abc/efgh/jkl"]
    assert set_ranges_on_splitted(s, ranges, parts, include_separator=True)
    assert get_ranges(parts[0]) == []
    assert get_ranges(parts[1]) == [
        TaintRange(0, 1, Source("/abc", "sample_value", OriginType.PARAMETER)),
    ]
    assert get_ranges(parts[2]) == [
        TaintRange(0, 3, Source("abc", "sample_value", OriginType.PARAMETER)),
        TaintRange(2, 5, Source("c/efg", "sample_value", OriginType.PARAMETER)),
        TaintRange(6, 5, Source("gh/jk", "sample_value", OriginType.PARAMETER)),
    ]


def test_set_ranges_on_splitted_ospathsplitroot_posix_startwithtwoslashes_path():
    s = "//abc/efgh/jkl"
    range1 = _build_sample_range(0, 2, "//")
    range2 = _build_sample_range(2, 3, "abc")
    range3 = _build_sample_range(5, 4, "/efg")
    range4 = _build_sample_range(9, 4, "h/jk")
    set_ranges(s, (range1, range2, range3, range4))
    ranges = get_ranges(s)
    assert ranges

    # We emulate what os.path.splitroot would do on posix starting with double slash instead of calling it
    parts = ["", "//", "abc/efgh/jkl"]
    assert set_ranges_on_splitted(s, ranges, parts, include_separator=True)
    assert get_ranges(parts[0]) == []
    assert get_ranges(parts[1]) == [
        TaintRange(0, 2, Source("//", "sample_value", OriginType.PARAMETER)),
    ]
    assert get_ranges(parts[2]) == [
        TaintRange(0, 3, Source("abc", "sample_value", OriginType.PARAMETER)),
        TaintRange(3, 4, Source("/efg", "sample_value", OriginType.PARAMETER)),
        TaintRange(7, 4, Source("h/jk", "sample_value", OriginType.PARAMETER)),
    ]


def test_set_ranges_on_splitted_ospathsplitroot_posix_startwiththreeslashes_path():
    s = "///abc/efgh/jkl"
    range1 = _build_sample_range(0, 3, "///")
    range2 = _build_sample_range(3, 3, "abc")
    range3 = _build_sample_range(6, 4, "/efg")
    range4 = _build_sample_range(10, 4, "h/jk")
    set_ranges(s, (range1, range2, range3, range4))
    ranges = get_ranges(s)
    assert ranges

    # We emulate what os.path.splitroot would do on posix starting with triple slash instead of calling it
    parts = ["", "/", "//abc/efgh/jkl"]
    assert set_ranges_on_splitted(s, ranges, parts, include_separator=True)
    assert get_ranges(parts[0]) == []
    assert get_ranges(parts[1]) == [
        TaintRange(0, 1, Source("/", "sample_value", OriginType.PARAMETER)),
    ]
    assert get_ranges(parts[2]) == [
        TaintRange(0, 2, Source("///", "sample_value", OriginType.PARAMETER)),
        TaintRange(2, 3, Source("abc", "sample_value", OriginType.PARAMETER)),
        TaintRange(5, 4, Source("/efg", "sample_value", OriginType.PARAMETER)),
        TaintRange(9, 4, Source("h/jk", "sample_value", OriginType.PARAMETER)),
    ]


def test_set_ranges_on_splitted_bytes() -> None:
    s = b"abc|efgh|ijkl"
    range1 = _build_sample_range(0, 2, "first")  # ab -> 0, 2
    range2 = _build_sample_range(5, 1, "second")  # f -> 1, 1
    range3 = _build_sample_range(11, 2, "third")  # jkl -> 1, 3
    set_ranges(s, (range1, range2, range3))
    ranges = get_ranges(s)
    assert ranges

    parts = s.split(b"|")
    assert set_ranges_on_splitted(s, ranges, parts)
    assert get_ranges(parts[0]) == [TaintRange(0, 2, Source("first", "sample_value", OriginType.PARAMETER))]
    assert get_ranges(parts[1]) == [TaintRange(1, 1, Source("second", "sample_value", OriginType.PARAMETER))]
    assert get_ranges(parts[2]) == [TaintRange(2, 2, Source("third", "sample_value", OriginType.PARAMETER))]


def test_set_ranges_on_splitted_bytearray() -> None:
    s = bytearray(b"abc|efgh|ijkl")
    range1 = _build_sample_range(0, 2, "ab")
    range2 = _build_sample_range(5, 1, "f")
    range3 = _build_sample_range(5, 6, "fgh|ij")

    set_ranges(s, (range1, range2, range3))
    ranges = get_ranges(s)
    assert ranges

    parts = s.split(b"|")
    assert set_ranges_on_splitted(s, ranges, parts)
    assert get_ranges(parts[0]) == [TaintRange(0, 2, Source("ab", "sample_value", OriginType.PARAMETER))]
    assert get_ranges(parts[1]) == [
        TaintRange(1, 1, Source("f", "sample_value", OriginType.PARAMETER)),
        TaintRange(1, 4, Source("second", "sample_value", OriginType.PARAMETER)),
    ]
    assert get_ranges(parts[2]) == [TaintRange(0, 2, Source("third", "sample_value", OriginType.PARAMETER))]


def test_set_ranges_on_splitted_wrong_args():
    s = "12345"
    range1 = _build_sample_range(1, 3, "234")
    set_ranges(s, (range1,))
    ranges = get_ranges(s)

    assert not set_ranges_on_splitted(s, [], ["123", 45])
    assert not set_ranges_on_splitted("", ranges, ["123", 45])
    assert not set_ranges_on_splitted(s, ranges, [])
    parts = ["123", 45]
    set_ranges_on_splitted(s, ranges, parts)
    ranges = get_ranges(parts[0])
    assert ranges == [TaintRange(1, 3, Source("123", "sample_value", OriginType.PARAMETER))]
