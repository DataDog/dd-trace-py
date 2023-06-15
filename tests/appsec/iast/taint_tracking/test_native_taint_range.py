import sys

import pytest


try:
    from ddtrace.appsec.iast._taint_tracking import OriginType
    from ddtrace.appsec.iast._taint_tracking import Source
    from ddtrace.appsec.iast._taint_tracking import TaintRange
    from ddtrace.appsec.iast._taint_tracking import are_all_text_all_ranges
    from ddtrace.appsec.iast._taint_tracking import get_range_by_hash
    from ddtrace.appsec.iast._taint_tracking import get_ranges
    from ddtrace.appsec.iast._taint_tracking import is_notinterned_notfasttainted_unicode
    from ddtrace.appsec.iast._taint_tracking import set_fast_tainted_if_notinterned_unicode
    from ddtrace.appsec.iast._taint_tracking import set_ranges
    from ddtrace.appsec.iast._taint_tracking import shift_taint_range
    from ddtrace.appsec.iast._taint_tracking import shift_taint_ranges
except (ImportError, AttributeError):
    pytest.skip("IAST not supported for this Python version", allow_module_level=True)


def test_source_origin_refcount():
    s1 = Source(name="name", value="val", origin=OriginType.COOKIE)
    assert sys.getrefcount(s1) - 1 == 1  # getrefcount takes 1 while counting
    s2 = s1
    assert sys.getrefcount(s1) - 1 == 2
    s3 = s1
    assert sys.getrefcount(s1) - 1 == 3
    del s2
    assert sys.getrefcount(s1) - 1 == 2
    # TaintRange does not increase refcount but should keep it alive
    tr_sub = TaintRange(0, 1, s1)
    assert sys.getrefcount(s1) - 1 == 2
    del s1
    assert sys.getrefcount(s3) - 1 == 1
    assert sys.getrefcount(tr_sub.source) - 1 == 1
    del s3
    assert sys.getrefcount(tr_sub.source) - 1 == 1
    _ = TaintRange(1, 2, tr_sub.source)
    assert sys.getrefcount(tr_sub.source) - 1 == 1


_SOURCE1 = Source(name="name", value="value", origin=OriginType.COOKIE)
_SOURCE2 = Source(name="name2", value="value2", origin=OriginType.BODY)

_RANGE1 = TaintRange(0, 2, _SOURCE1)
_RANGE2 = TaintRange(1, 3, _SOURCE2)


def test_unicode_fast_tainting():
    s = "somestr" * 4000
    s_check = "somestr" * 4000
    # Check that s is not interned since fast tainting only works on non-interned strings
    assert s is not s_check
    assert is_notinterned_notfasttainted_unicode(s)

    set_fast_tainted_if_notinterned_unicode(s)
    assert not is_notinterned_notfasttainted_unicode(s)

    b = b"foobar" * 4000
    assert not is_notinterned_notfasttainted_unicode(b)
    set_fast_tainted_if_notinterned_unicode(b)
    assert not is_notinterned_notfasttainted_unicode(b)

    ba = bytearray(b"sfdsdfsdf" * 4000)
    assert not is_notinterned_notfasttainted_unicode(ba)
    set_fast_tainted_if_notinterned_unicode(ba)
    assert not is_notinterned_notfasttainted_unicode(ba)

    c = 12345
    assert not is_notinterned_notfasttainted_unicode(c)
    set_fast_tainted_if_notinterned_unicode(c)
    assert not is_notinterned_notfasttainted_unicode(c)


def test_set_get_ranges_str():
    s1 = "abcde😁"
    s2 = "defg"
    set_ranges(s1, [_RANGE1, _RANGE2])
    assert get_ranges(s1) == [_RANGE1, _RANGE2]
    assert not get_ranges(s2)


def test_set_get_ranges_other():
    s1 = 12345
    s2 = None
    set_ranges(s1, [_RANGE1, _RANGE2])
    set_ranges(s2, [_RANGE1, _RANGE2])
    assert not get_ranges(s1)
    assert not get_ranges(s2)


def test_set_get_ranges_bytes():
    b1 = b"ABCDE"
    b2 = b"DEFG"
    set_ranges(b1, [_RANGE2, _RANGE1])
    assert get_ranges(b1) == [_RANGE2, _RANGE1]
    assert not get_ranges(b2) == [_RANGE2, _RANGE1]


def test_set_get_ranges_bytearray():
    b1 = bytearray(b"abcdef")
    b2 = bytearray(b"abcdef")
    set_ranges(b1, [_RANGE1, _RANGE2])
    assert get_ranges(b1) == [_RANGE1, _RANGE2]
    assert not get_ranges(b2) == [_RANGE1, _RANGE2]


def test_shift_taint_ranges():
    r1 = TaintRange(0, 2, _SOURCE1)
    r1_shifted = shift_taint_range(r1, 2)
    assert r1_shifted == TaintRange(2, 2, _SOURCE1)
    assert r1_shifted != r1

    r2 = TaintRange(1, 3, _SOURCE1)
    r3 = TaintRange(4, 6, _SOURCE2)
    r2_shifted, r3_shifted = shift_taint_ranges([r2, r3], 2)
    assert r2_shifted == TaintRange(3, 3, _SOURCE1)
    assert r3_shifted == TaintRange(6, 6, _SOURCE1)


def test_are_all_text_all_ranges():
    s1 = "abcdef"
    s2 = "ghijk"
    s3 = "xyzv"
    num = 123456
    source3 = Source(name="name3", value="value3", origin=OriginType.COOKIE)
    source4 = Source(name="name4", value="value4", origin=OriginType.COOKIE)
    range3 = TaintRange(2, 3, source3)
    range4 = TaintRange(4, 5, source4)
    set_ranges(s1, [_RANGE1, _RANGE2])
    set_ranges(s2, [range3, _RANGE2])
    set_ranges(s3, [range4, _RANGE1])
    all_ranges, candidate_ranges = are_all_text_all_ranges(s1, (s2, s3, num))
    # Ranges are inserted at the start except the candidate ones that are appended
    assert all_ranges == [range3, _RANGE2, range4, _RANGE1, _RANGE1, _RANGE2]
    assert candidate_ranges == [_RANGE1, _RANGE2]


def test_get_range_by_hash():
    hash_r1 = hash(_RANGE1)
    assert hash_r1 == _RANGE1.__hash__()
    hash_r2_call = hash(_RANGE2)
    hash_r2_method = _RANGE2.__hash__()
    assert hash_r2_call == hash_r2_method
    assert hash_r1 != hash_r2_call
    assert get_range_by_hash(hash_r1, [_RANGE1, _RANGE2]) == _RANGE1
    assert get_range_by_hash(hash_r2_call, [_RANGE1, _RANGE2]) == _RANGE2
