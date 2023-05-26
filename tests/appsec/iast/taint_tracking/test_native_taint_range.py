import array
import sys
import pytest

from ddtrace.appsec.iast._taint_tracking import (Source, OriginType, TaintRange,
                                                 set_ranges, get_ranges)

try:
    from ddtrace.appsec.iast._taint_tracking import (Source, OriginType, TaintRange,
        set_ranges, get_ranges, shift_taint_range, shift_taint_ranges, get_range_by_hash)
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
    tr_sub2 = TaintRange(1, 2, tr_sub.source)
    assert sys.getrefcount(tr_sub.source) - 1 == 1


_SOURCE1 = Source(name="name", value="value", origin=OriginType.COOKIE)
_SOURCE2 = Source(name="name2", value="value2", origin=OriginType.BODY)

_RANGE1 = TaintRange(0, 2, _SOURCE1)
_RANGE2 = TaintRange(1, 3, _SOURCE2)


def test_set_get_ranges_str():
    s1 = "abcde😁"
    s2 = "defg"
    set_ranges(s1, [_RANGE1, _RANGE2])
    assert get_ranges(s1) == [_RANGE1, _RANGE2]
    assert not get_ranges(s2)


@pytest.mark.skip(reason="FIXME: currently not detecting types")
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
    b1 = bytearray(b'abcdef')
    b2 = bytearray(b'abcdef')
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


# FIXME: todo
# def test_are_all_textgg_all_ranges():
#     s1 = "abcdef"
#     s2 = "ghijk"
#     set_ranges(s1, [_RANGE1, _RANGE2])
#     set_ranges(s2, [_RANGE2, _RANGE2])


def test_get_range_by_hash():
    hash_r1 = hash(_RANGE1)
    assert hash_r1 == _RANGE1.__hash__()
    hash_r2_call = hash(_RANGE2)
    hash_r2_method = _RANGE2.__hash__()
    assert hash_r2_call == hash_r2_method
    assert hash_r1 != hash_r2_call
    assert get_range_by_hash(hash_r1, [_RANGE1, _RANGE2]) == _RANGE1
    assert get_range_by_hash(hash_r2_call, [_RANGE1, _RANGE2]) == _RANGE2
