from hypothesis import given
from hypothesis.strategies import one_of
import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted
import ddtrace.appsec._iast._taint_tracking.aspects as ddtrace_aspects
from tests.appsec.iast.iast_utils import string_strategies


@given(one_of(string_strategies))
def test_strip_aspect(text):
    assert ddtrace_aspects.strip_aspect(None, 1, text) == text.strip()


@given(one_of(string_strategies))
def test_rstrip_aspect(text):
    assert ddtrace_aspects.rstrip_aspect(None, 1, text) == text.rstrip()


@given(one_of(string_strategies))
def test_lstrip_aspect(text):
    assert ddtrace_aspects.lstrip_aspect(None, 1, text) == text.lstrip()


@pytest.mark.parametrize(
    "obj1,obj2,should_be_tainted,expected_start,expected_length",
    [
        (",;,;aaa", ",;:", True, 0, 3),
        (",;,;aaa", "123", True, 0, 7),
        ("aaa,;,;", ",;:", True, 0, 3),
        (",;,;aaa,;,;", ",;:", True, 0, 3),
        ("   hello   ", None, True, 0, 5),
        ("\t\ntext\t\n", None, True, 0, 4),
        ("xxxhelloxxx", "x", True, 0, 5),
        ("", "abc", False, 0, 0),
        ("abc", "", True, 0, 3),
        ("   ", None, False, 0, 0),
        ("...###text###...", ".#", True, 0, 4),
        ("абвгтекстабвг", "абвг", True, 0, 5),
        ("🌟✨text🌟✨", "🌟✨", True, 0, 4),
        ("\u200b\u200btext\u200b", "\u200b", True, 0, 4),
        ("  \t\n\r text \t\n\r  ", None, True, 0, 4),
        ("...text...", ".", True, 0, 4),
    ],
)
def test_strip_aspect_tainted(obj1, obj2, should_be_tainted, expected_start, expected_length):
    """Test strip aspect with various input combinations."""
    obj1 = taint_pyobject(
        pyobject=obj1,
        source_name="test_strip_aspect",
        source_value=obj1,
        source_origin=OriginType.PARAMETER,
    )

    result = ddtrace_aspects.strip_aspect(None, 1, obj1, obj2)
    if obj2 is None:
        assert result == obj1.strip()
    else:
        assert result == obj1.strip(obj2)

    assert is_pyobject_tainted(result) == should_be_tainted
    if should_be_tainted:
        ranges = get_tainted_ranges(result)
        assert ranges[0].start == expected_start
        assert ranges[0].length == expected_length


@pytest.mark.parametrize(
    "obj1,obj2,expected_start,expected_length",
    [
        (",;,;aaa", ",;:", 0, 3),
        ("aaa,;,;", ",;:", 0, 3),
        (",;,;aaa,;,;", ",;:", 0, 3),
        ("   hello   ", None, 0, 5),
        ("\t\n text \t\n", None, 0, 4),
        ("xxxhelloxxx", "x", 0, 5),
        ("...###text###...", ".#", 0, 4),
        ("абвгтекстабвг", "абвг", 0, 5),
        ("...text...", ".", 0, 4),
    ],
)
@pytest.mark.parametrize("ranges_position", list(range(1, 7)))
def test_strip_aspect_tainted_multiple_ranges(obj1, obj2, expected_start, expected_length, ranges_position):
    from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect

    concat_obj1 = add_aspect(
        taint_pyobject(
            pyobject=obj1[:ranges_position],
            source_name="obj1_pos1",
            source_value=obj1[:ranges_position],
            source_origin=OriginType.PARAMETER,
        ),
        taint_pyobject(
            pyobject=obj1[ranges_position:],
            source_name="obj1_pos2",
            source_value=obj1[ranges_position:],
            source_origin=OriginType.PARAMETER,
        ),
    )

    result = ddtrace_aspects.strip_aspect(None, 1, concat_obj1, obj2)
    if obj2 is None:
        assert result == concat_obj1.strip()
    else:
        assert result == concat_obj1.strip(obj2)

    assert is_pyobject_tainted(result)

    assert len(get_tainted_ranges(result))


@pytest.mark.parametrize(
    "obj1,obj2,should_be_tainted,expected_start,expected_length",
    [
        ("aaa,;,;", ",;:", True, 0, 3),
        ("text.....", ".", True, 0, 4),
        ("text   ", None, True, 0, 4),
        ("hello\t\n\r", None, True, 0, 5),
        ("text\u200b\u200b", "\u200b", True, 0, 4),
        ("text🌟✨", "🌟✨", True, 0, 4),
        ("", "abc", False, 0, 0),
        ("abc", "", True, 0, 3),
        ("   ", None, False, 0, 0),
        ("text...###", ".#", True, 0, 4),
        ("текстабвг", "абвг", True, 0, 5),
        ("text\t \n", None, True, 0, 4),
        ("hello...", ".", True, 0, 5),
        ("text   \t\n\r", None, True, 0, 4),
    ],
)
def test_rstrip_aspect_tainted(obj1, obj2, should_be_tainted, expected_start, expected_length):
    """Test rstrip aspect with various input combinations."""
    obj1 = taint_pyobject(
        pyobject=obj1,
        source_name="test_rstrip_aspect",
        source_value=obj1,
        source_origin=OriginType.PARAMETER,
    )

    result = ddtrace_aspects.rstrip_aspect(None, 1, obj1, obj2)
    if obj2 is None:
        assert result == obj1.rstrip()
    else:
        assert result == obj1.rstrip(obj2)

    assert is_pyobject_tainted(result) == should_be_tainted
    if should_be_tainted:
        ranges = get_tainted_ranges(result)
        assert ranges[0].start == expected_start
        assert ranges[0].length == expected_length


@pytest.mark.parametrize(
    "obj1,obj2",
    [
        ("aaaa,;,;", ",;:"),
        ("text.....", "."),
        ("text   ", None),
        ("hello\t\n\r", None),
        ("text\u200b\u200b", "\u200b"),
        ("text🌟✨", "🌟✨"),
        ("abcd", ""),
        ("text...###", ".#"),
        ("текстабвг", "абвг"),
        ("text\t \n", None),
        ("hello...", "."),
        ("text   \t\n\r", None),
    ],
)
@pytest.mark.parametrize("ranges_position", list(range(1, 5)))
def test_rstrip_aspect_tainted_multiple_ranges(obj1, obj2, ranges_position):
    from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect

    concat_obj1 = add_aspect(
        taint_pyobject(
            pyobject=obj1[:ranges_position],
            source_name="obj1_pos1",
            source_value=obj1[:ranges_position],
            source_origin=OriginType.PARAMETER,
        ),
        taint_pyobject(
            pyobject=obj1[ranges_position:],
            source_name="obj1_pos2",
            source_value=obj1[ranges_position:],
            source_origin=OriginType.PARAMETER,
        ),
    )
    result = ddtrace_aspects.rstrip_aspect(None, 1, concat_obj1, obj2)
    if obj2 is None:
        assert result == concat_obj1.rstrip()
    else:
        assert result == concat_obj1.rstrip(obj2)

    assert is_pyobject_tainted(result)

    ranges = get_tainted_ranges(result)
    assert ranges

    for i in range(len(ranges)):
        if i == 0:
            len_range = ranges_position
            start = 0
        else:
            start = ranges_position
            len_range = len(result) - ranges_position
        assert ranges[i].start == start
        assert ranges[i].length == len_range


@pytest.mark.parametrize(
    "obj1,obj2,should_be_tainted,expected_start,expected_length",
    [
        (",;,;aaa", ",;:", True, 0, 3),
        (".....text", ".", True, 0, 4),
        ("   text", None, True, 0, 4),
        ("\t\n\rtext", None, True, 0, 4),
        ("\u200b\u200btext", "\u200b", True, 0, 4),
        ("🌟✨text", "🌟✨", True, 0, 4),
        ("", "abc", False, 0, 0),
        ("abc", "", True, 0, 3),
        ("   ", None, False, 0, 0),
        ("...###text", ".#", True, 0, 4),
        ("абвгтекст", "абвг", True, 0, 5),
        ("\t \ntext", None, True, 0, 4),
        ("...hello", ".", True, 0, 5),
        ("\t\n\r   text", None, True, 0, 4),
    ],
)
def test_lstrip_aspect_tainted(obj1, obj2, should_be_tainted, expected_start, expected_length):
    """Test lstrip aspect with various input combinations."""
    obj1 = taint_pyobject(
        pyobject=obj1,
        source_name="test_lstrip_aspect",
        source_value=obj1,
        source_origin=OriginType.PARAMETER,
    )

    result = ddtrace_aspects.lstrip_aspect(None, 1, obj1, obj2)
    if obj2 is None:
        assert result == obj1.lstrip()
    else:
        assert result == obj1.lstrip(obj2)

    assert is_pyobject_tainted(result) == should_be_tainted
    if should_be_tainted:
        ranges = get_tainted_ranges(result)
        assert ranges[0].start == expected_start
        assert ranges[0].length == expected_length


@pytest.mark.parametrize(
    "obj1,obj2",
    [
        (",;,;aaa", ",;:"),
        (".....text", "."),
        ("   text", None),
        ("\t\n\rtext", None),
        ("-_text", "-_"),
        ("abccdefg", ""),
        ("...###text", ".#"),
        ("абвгтекст", "абвг"),
        ("\t \ntext", None),
        ("...hellos", "."),
        ("\t\n\r   text1234", None),
    ],
)
@pytest.mark.parametrize("ranges_position", list(range(1, 5)))
def test_lstrip_aspect_tainted_multiple_ranges(obj1, obj2, ranges_position):
    from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect

    concat_obj1 = add_aspect(
        taint_pyobject(
            pyobject=obj1[:ranges_position],
            source_name="obj1_pos1",
            source_value=obj1[:ranges_position],
            source_origin=OriginType.PARAMETER,
        ),
        taint_pyobject(
            pyobject=obj1[ranges_position:],
            source_name="obj1_pos2",
            source_value=obj1[ranges_position:],
            source_origin=OriginType.PARAMETER,
        ),
    )

    result = ddtrace_aspects.lstrip_aspect(None, 1, concat_obj1, obj2)

    if obj2 is None:
        assert result == concat_obj1.lstrip()
    else:
        assert result == concat_obj1.lstrip(obj2)

    assert is_pyobject_tainted(result)

    ranges = get_tainted_ranges(result)
    assert ranges

    if len(ranges) == 1:
        assert ranges[0].start == 0
        assert ranges[0].length == len(result)
    elif len(ranges) == 2:
        for i in range(len(ranges)):
            if i == 0:
                len_range = ranges_position - (len(concat_obj1) - len(result))
                start = 0
            else:
                start = ranges_position - (len(concat_obj1) - len(result))
                len_range = len(result) - start
            assert ranges[i].start == start, f"Assertion error: R[{ranges[i]}][{i}] == {start}"
            assert ranges[i].length == len_range, f"Assertion error: R[{ranges[i]}][{i}] == {len_range}"
    else:
        pytest.xfail(f"Invalid ranges: {ranges}")


def test_strip_with_multiple_ranges():
    """Test strip aspect with text containing multiple tainted ranges."""
    text = "...hello...world..."
    obj1 = taint_pyobject(
        pyobject=text,
        source_name="test_multiple_ranges",
        source_value=text,
        source_origin=OriginType.PARAMETER,
    )

    # Test all three strip functions
    strip_result = ddtrace_aspects.strip_aspect(None, 1, obj1, ".")
    rstrip_result = ddtrace_aspects.rstrip_aspect(None, 1, obj1, ".")
    lstrip_result = ddtrace_aspects.lstrip_aspect(None, 1, obj1, ".")

    # Verify results
    assert strip_result == "hello...world"
    assert rstrip_result == "...hello...world"
    assert lstrip_result == "hello...world..."

    # Verify taint ranges are preserved
    assert is_pyobject_tainted(strip_result)
    assert is_pyobject_tainted(rstrip_result)
    assert is_pyobject_tainted(lstrip_result)
