from hypothesis import given
from hypothesis.strategies import one_of
import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges
from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
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
        # Basic cases
        (",;,;aaa", ",;:", True, 0, 3),  # Original test case
        ("aaa,;,;", ",;:", True, 0, 3),  # Original test case
        (",;,;aaa,;,;", ",;:", True, 0, 3),  # Original test case
        # Edge cases with different characters
        ("   hello   ", None, True, 0, 5),  # Default whitespace stripping
        ("\t\ntext\t\n", None, True, 0, 4),  # Tabs and newlines
        ("xxxhelloxxx", "x", True, 0, 5),  # Single character strip
        # Corner cases
        ("", "abc", False, 0, 0),  # Empty string
        ("abc", "", True, 0, 3),  # Empty strip chars
        ("   ", None, False, 0, 0),  # Only whitespace
        # Mixed cases
        ("...###text###...", ".#", True, 0, 4),  # Multiple strip chars
        ("абвгтекстабвг", "абвг", True, 0, 5),  # Unicode characters
        ("🌟✨text🌟✨", "🌟✨", True, 0, 4),  # Emojis
        # Special cases
        ("\u200b\u200btext\u200b", "\u200b", True, 0, 4),  # Zero-width spaces
        ("  \t\n\r text \t\n\r  ", None, True, 0, 4),  # All whitespace types
        ("...text...", ".", True, 0, 4),  # Repeated chars
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
    "obj1,obj2,should_be_tainted,expected_start,expected_length",
    [
        # Basic cases
        ("aaa,;,;", ",;:", True, 0, 3),  # Basic right strip
        ("text.....", ".", True, 0, 4),  # Single char right strip
        ("text   ", None, True, 0, 4),  # Default whitespace
        # Edge cases
        ("hello\t\n\r", None, True, 0, 5),  # Various whitespace
        ("text\u200b\u200b", "\u200b", True, 0, 4),  # Zero-width spaces
        ("text🌟✨", "🌟✨", True, 0, 4),  # Emojis at end
        # Corner cases
        ("", "abc", False, 0, 0),  # Empty string
        ("abc", "", True, 0, 3),  # Empty strip chars
        ("   ", None, False, 0, 0),  # Only whitespace
        # Mixed cases
        ("text...###", ".#", True, 0, 4),  # Multiple strip chars
        ("текстабвг", "абвг", True, 0, 5),  # Unicode characters
        # Special cases
        ("text\t \n", None, True, 0, 4),  # Mixed whitespace at end
        ("hello...", ".", True, 0, 5),  # Repeated chars
        ("text   \t\n\r", None, True, 0, 4),  # All whitespace types
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
    "obj1,obj2,should_be_tainted,expected_start,expected_length",
    [
        # Basic cases
        (",;,;aaa", ",;:", True, 0, 3),  # Basic left strip
        (".....text", ".", True, 0, 4),  # Single char left strip
        ("   text", None, True, 0, 4),  # Default whitespace
        # Edge cases
        ("\t\n\rtext", None, True, 0, 4),  # Various whitespace
        ("\u200b\u200btext", "\u200b", True, 0, 4),  # Zero-width spaces
        ("🌟✨text", "🌟✨", True, 0, 4),  # Emojis at start
        # Corner cases
        ("", "abc", False, 0, 0),  # Empty string
        ("abc", "", True, 0, 3),  # Empty strip chars
        ("   ", None, False, 0, 0),  # Only whitespace
        # Mixed cases
        ("...###text", ".#", True, 0, 4),  # Multiple strip chars
        ("абвгтекст", "абвг", True, 0, 5),  # Unicode characters
        # Special cases
        ("\t \ntext", None, True, 0, 4),  # Mixed whitespace at start
        ("...hello", ".", True, 0, 5),  # Repeated chars
        ("\t\n\r   text", None, True, 0, 4),  # All whitespace types
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
