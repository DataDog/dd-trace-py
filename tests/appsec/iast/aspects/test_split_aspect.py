from ddtrace.appsec._iast._taint_tracking import TaintRange
from ddtrace.appsec._iast._taint_tracking import _aspect_rsplit
from ddtrace.appsec._iast._taint_tracking import _aspect_split
from ddtrace.appsec._iast._taint_tracking import _aspect_splitlines
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import Source
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import get_ranges
from ddtrace.appsec._iast._taint_tracking._native.taint_tracking import set_ranges
from tests.appsec.iast.aspects.test_aspect_helpers import _build_sample_range


# These tests are simple ones testing the calls and replacements since most of the
# actual testing is in test_aspect_helpers' test for set_ranges_on_splitted which these
# functions call internally.
def test_aspect_split_simple():
    s = "abc def"
    range1 = _build_sample_range(0, 3, "abc")
    range2 = _build_sample_range(3, 4, " def")
    set_ranges(s, (range1, range2))
    ranges = get_ranges(s)
    assert ranges
    res = _aspect_split(s)
    assert res == ["abc", "def"]
    assert get_ranges(res[0]) == [range1]
    assert get_ranges(res[1]) == [TaintRange(0, 3, Source(" def", "sample_value", OriginType.PARAMETER))]


def test_aspect_rsplit_simple():
    s = "abc def"
    range1 = _build_sample_range(0, 3, "abc")
    range2 = _build_sample_range(3, 4, " def")
    set_ranges(s, (range1, range2))
    ranges = get_ranges(s)
    assert ranges
    res = _aspect_rsplit(s)
    assert res == ["abc", "def"]
    assert get_ranges(res[0]) == [range1]
    assert get_ranges(res[1]) == [TaintRange(0, 3, Source(" def", "sample_value", OriginType.PARAMETER))]


def test_aspect_split_with_separator():
    s = "abc:def"
    range1 = _build_sample_range(0, 3, "abc")
    range2 = _build_sample_range(3, 4, ":def")
    set_ranges(s, (range1, range2))
    ranges = get_ranges(s)
    assert ranges
    res = _aspect_split(s, ":")
    assert res == ["abc", "def"]
    assert get_ranges(res[0]) == [range1]
    assert get_ranges(res[1]) == [TaintRange(0, 3, Source(":def", "sample_value", OriginType.PARAMETER))]


def test_aspect_rsplit_with_separator():
    s = "abc:def"
    range1 = _build_sample_range(0, 3, "abc")
    range2 = _build_sample_range(3, 4, ":def")
    set_ranges(s, (range1, range2))
    ranges = get_ranges(s)
    assert ranges
    res = _aspect_rsplit(s, ":")
    assert res == ["abc", "def"]
    assert get_ranges(res[0]) == [range1]
    assert get_ranges(res[1]) == [TaintRange(0, 3, Source(":def", "sample_value", OriginType.PARAMETER))]


def test_aspect_split_with_maxsplit():
    s = "abc def ghi"
    range1 = _build_sample_range(0, 3, "abc")
    range2 = _build_sample_range(3, 4, " def")
    range3 = _build_sample_range(7, 4, " ghi")
    set_ranges(s, (range1, range2, range3))
    ranges = get_ranges(s)
    assert ranges
    res = _aspect_split(s, maxsplit=1)
    assert res == ["abc", "def ghi"]
    assert get_ranges(res[0]) == [range1]
    assert get_ranges(res[1]) == [
        TaintRange(0, 3, Source(" def", "sample_value", OriginType.PARAMETER)),
        TaintRange(3, 4, Source(" ghi", "sample_value", OriginType.PARAMETER)),
    ]

    res = _aspect_split(s, maxsplit=2)
    assert res == ["abc", "def", "ghi"]
    assert get_ranges(res[0]) == [range1]
    assert get_ranges(res[1]) == [TaintRange(0, 3, Source(" def", "sample_value", OriginType.PARAMETER))]
    assert get_ranges(res[2]) == [TaintRange(0, 3, Source(" ghi", "sample_value", OriginType.PARAMETER))]

    res = _aspect_split(s, maxsplit=0)
    assert res == ["abc def ghi"]
    assert get_ranges(res[0]) == [range1, range2, range3]


def test_aspect_rsplit_with_maxsplit():
    s = "abc def ghi"
    range1 = _build_sample_range(0, 3, "abc")
    range2 = _build_sample_range(3, 4, " def")
    range3 = _build_sample_range(7, 4, " ghi")
    set_ranges(s, (range1, range2, range3))
    ranges = get_ranges(s)
    assert ranges
    res = _aspect_rsplit(s, maxsplit=1)
    assert res == ["abc def", "ghi"]
    assert get_ranges(res[0]) == [
        range1,
        TaintRange(3, 4, Source(" def", "sample_value", OriginType.PARAMETER)),
    ]
    assert get_ranges(res[1]) == [TaintRange(0, 3, Source(" ghi", "sample_value", OriginType.PARAMETER))]
    res = _aspect_rsplit(s, maxsplit=2)
    assert res == ["abc", "def", "ghi"]
    assert get_ranges(res[0]) == [range1]
    assert get_ranges(res[1]) == [TaintRange(0, 3, Source(" def", "sample_value", OriginType.PARAMETER))]
    assert get_ranges(res[2]) == [TaintRange(0, 3, Source(" ghi", "sample_value", OriginType.PARAMETER))]

    res = _aspect_rsplit(s, maxsplit=0)
    assert res == ["abc def ghi"]
    assert get_ranges(res[0]) == [range1, range2, range3]


def test_aspect_splitlines_simple():
    s = "abc\ndef"
    range1 = _build_sample_range(0, 3, "abc")
    range2 = _build_sample_range(3, 4, " def")
    set_ranges(s, (range1, range2))
    ranges = get_ranges(s)
    assert ranges
    res = _aspect_splitlines(s)
    assert res == ["abc", "def"]
    assert get_ranges(res[0]) == [range1]
    assert get_ranges(res[1]) == [TaintRange(0, 3, Source(" def", "sample_value", OriginType.PARAMETER))]


def test_aspect_splitlines_keepend_true():
    s = "abc\ndef\nhij\n"
    range1 = _build_sample_range(0, 4, "abc\n")
    range2 = _build_sample_range(4, 4, "def\n")
    range3 = _build_sample_range(8, 4, "hij\n")
    set_ranges(s, (range1, range2, range3))
    ranges = get_ranges(s)
    assert ranges
    res = _aspect_splitlines(s, True)
    assert res == ["abc\n", "def\n", "hij\n"]
    assert get_ranges(res[0]) == [range1]
    assert get_ranges(res[1]) == [TaintRange(0, 4, Source("def\n", "sample_value", OriginType.PARAMETER))]
    assert get_ranges(res[2]) == [TaintRange(0, 4, Source("hij\n", "sample_value", OriginType.PARAMETER))]
