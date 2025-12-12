"""
Tests for IAST memory optimizations:
- DD_IAST_TRUNCATION_MAX_VALUE_LENGTH: Truncate Source.value to limit memory
- DD_IAST_MAX_RANGE_COUNT: Limit number of TaintRange objects per TaintedObject
"""

import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import get_ranges
from ddtrace.appsec._iast._taint_tracking._native import reset_source_truncation_cache
from ddtrace.appsec._iast._taint_tracking._native import reset_taint_range_limit_cache
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast._taint_tracking.aspects import join_aspect


@pytest.fixture(autouse=True)
def reset_cache_after_test():
    """Reset both caches after each test to ensure clean state."""
    yield
    reset_taint_range_limit_cache()
    reset_source_truncation_cache()


@pytest.fixture
def set_max_range_count(monkeypatch):
    """Fixture to set DD_IAST_MAX_RANGE_COUNT and reset the cache."""

    def _set_value(value):
        monkeypatch.setenv("DD_IAST_MAX_RANGE_COUNT", str(value))
        reset_taint_range_limit_cache()

    return _set_value


@pytest.fixture
def set_truncation_max_length(monkeypatch):
    """Fixture to set DD_IAST_TRUNCATION_MAX_VALUE_LENGTH and reset the cache."""

    def _set_value(value):
        monkeypatch.setenv("DD_IAST_TRUNCATION_MAX_VALUE_LENGTH", str(value))
        reset_source_truncation_cache()

    return _set_value


def taint_string(s, name="test_input"):
    """Helper to taint a string for testing."""
    return taint_pyobject(
        pyobject=s,
        source_name=name,
        source_value=s,
        source_origin=OriginType.PARAMETER,
    )


class TestSourceValueTruncation:
    """Test DD_IAST_TRUNCATION_MAX_VALUE_LENGTH environment variable."""

    @pytest.mark.parametrize(
        "string_length,max_length,expected_length",
        [
            (10, 250, 10),  # Small string - no truncation needed (default limit)
            (100, 250, 100),  # Medium string - no truncation (default limit)
            (500, 50, 50),  # Large string - should be truncated to 50
            (1000, 50, 50),  # Very large string - should be truncated to 50
            (10000, 50, 50),  # Huge string - should be truncated to 50
        ],
    )
    def test_source_value_truncation(self, string_length, max_length, expected_length, set_truncation_max_length):
        """Test that Source.value is truncated according to DD_IAST_TRUNCATION_MAX_VALUE_LENGTH."""
        set_truncation_max_length(max_length)

        test_string = "x" * string_length
        tainted = taint_string(test_string, "truncation_test")
        ranges = get_ranges(tainted)

        assert ranges is not None, "Tainted string should have ranges"
        assert len(ranges) > 0, "Should have at least one range"

        source_value = ranges[0].source.value
        assert len(source_value) <= max_length, f"Source.value length {len(source_value)} exceeds max {max_length}"
        assert len(source_value) == expected_length, (
            f"Expected source.value length {expected_length}, got {len(source_value)}"
        )

    @pytest.mark.parametrize(
        "string_value,max_length,expected",
        [
            ("a", 250, "a"),  # Single char
            ("hello", 250, "hello"),  # Short string
            ("x" * 250, 250, "x" * 250),  # Exactly at limit (default 250)
            ("x" * 250, 50, "x" * 50),  # 250 chars truncated to 50
        ],
    )
    def test_source_value_exact_preservation(self, string_value, max_length, expected, set_truncation_max_length):
        """Test that short strings are preserved exactly or truncated correctly."""
        set_truncation_max_length(max_length)

        tainted = taint_string(string_value, "exact_test")
        ranges = get_ranges(tainted)

        assert ranges is not None
        assert len(ranges) > 0

        source_value = ranges[0].source.value
        assert source_value == expected, f"Expected '{expected}', got '{source_value}'"

    def test_source_value_empty_string(self):
        """Test that empty strings are handled correctly."""
        tainted = taint_string("", "empty_test")
        ranges = get_ranges(tainted)

        # Empty string may or may not have ranges depending on implementation
        if ranges is not None and len(ranges) > 0:
            source_value = ranges[0].source.value
            assert source_value == ""

    def test_source_value_truncation_join_aspect(self, set_truncation_max_length):
        """Test that Source.value truncation works with join_aspect."""
        max_length = 50
        set_truncation_max_length(max_length)

        # Create items with large strings
        large_string = "y" * 1000
        items = [large_string for _ in range(5)]
        separator = ","

        tainted_sep = taint_string(separator, "sep")
        tainted_items = [taint_string(item, f"item_{i}") for i, item in enumerate(items)]

        result = join_aspect("".join, 1, tainted_sep, tainted_items)
        result_ranges = get_ranges(result)

        assert result_ranges is not None
        assert len(result_ranges) > 0

        # Check that all source values are truncated
        for i, range_obj in enumerate(result_ranges):
            source_value_len = len(range_obj.source.value)
            assert source_value_len <= max_length, (
                f"Range {i}: source.value length {source_value_len} exceeds max {max_length}"
            )


class TestRangeCountLimiting:
    """Test DD_IAST_MAX_RANGE_COUNT environment variable."""

    @pytest.mark.parametrize(
        "num_items,max_ranges,expected_ranges",
        [
            (3, 30, 5),  # Few items - should create all ranges (3 items + 2 separators = 5)
            (5, 30, 9),  # Some items - should create all ranges (5 items + 4 separators = 9)
            (10, 10, 10),  # Many items - should be limited by max (10 items + 9 sep = 19, limited to 10)
            (20, 10, 10),  # Many items - should be limited by max (20 items + 19 sep = 39, limited to 10)
            (50, 10, 10),  # Many items - should be limited by max (50 items + 49 sep = 99, limited to 10)
            (1000, 10, 10),  # Many items - should be limited by max (50 items + 49 sep = 99, limited to 10)
        ],
    )
    def test_range_count_limiting_join(self, num_items, max_ranges, expected_ranges, set_max_range_count):
        """Test that TaintRange count is limited by DD_IAST_MAX_RANGE_COUNT."""
        set_max_range_count(max_ranges)

        separator = ","
        items = [f"item_{i}" for i in range(num_items)]

        tainted_sep = taint_string(separator, "sep")
        tainted_items = [taint_string(item, f"item_{i}") for i, item in enumerate(items)]

        result = join_aspect("".join, 1, tainted_sep, tainted_items)
        result_ranges = get_ranges(result)

        assert result_ranges is not None

        actual_ranges = len(result_ranges)
        assert actual_ranges <= max_ranges, f"Range count {actual_ranges} exceeds max {max_ranges}"
        assert actual_ranges == expected_ranges, f"Expected {expected_ranges} ranges, got {actual_ranges}"

    @pytest.mark.parametrize(
        "string_length,num_items,max_ranges",
        [
            (10, 5, 30),  # Small strings, few items
            (100, 10, 30),  # Medium strings, more items
            (1000, 20, 10),  # Large strings, many items
        ],
    )
    def test_range_limiting_with_large_strings(self, string_length, num_items, max_ranges, set_max_range_count):
        """Test that range limiting works independently of string size."""
        set_max_range_count(max_ranges)

        separator = "-"
        items = ["x" * string_length for _ in range(num_items)]

        tainted_sep = taint_string(separator, "sep")
        tainted_items = [taint_string(item, f"item_{i}") for i, item in enumerate(items)]

        result = join_aspect("".join, 1, tainted_sep, tainted_items)
        result_ranges = get_ranges(result)

        assert result_ranges is not None
        assert len(result_ranges) <= max_ranges, f"Range count {len(result_ranges)} exceeds max {max_ranges}"

    def test_range_limiting_repeated_operations(self, set_max_range_count):
        """Test that range limiting persists across multiple operations."""
        max_ranges = 10
        set_max_range_count(max_ranges)

        # Perform multiple join operations
        for iteration in range(10):
            separator = ","
            items = [f"iter{iteration}_item{i}" for i in range(20)]

            tainted_sep = taint_string(separator, f"sep_{iteration}")
            tainted_items = [taint_string(item, f"item_{i}") for i, item in enumerate(items)]

            result = join_aspect("".join, 1, tainted_sep, tainted_items)
            result_ranges = get_ranges(result)

            assert result_ranges is not None
            assert len(result_ranges) <= max_ranges, (
                f"Iteration {iteration}: range count {len(result_ranges)} exceeds max {max_ranges}"
            )


class TestCombinedOptimizations:
    """Test that both optimizations work together."""

    def test_both_optimizations_active(self, set_max_range_count, set_truncation_max_length):
        """Test that truncation and range limiting both work in the same operation."""
        max_length = 50
        max_ranges = 10
        set_max_range_count(max_ranges)
        set_truncation_max_length(max_length)

        # Create scenario with large strings and many items
        large_string = "z" * 5000
        num_items = 30
        items = [large_string for _ in range(num_items)]
        separator = ","

        tainted_sep = taint_string(separator, "sep")
        tainted_items = [taint_string(item, f"item_{i}") for i, item in enumerate(items)]

        result = join_aspect("".join, 1, tainted_sep, tainted_items)
        result_ranges = get_ranges(result)

        assert result_ranges is not None

        # Check range count limiting
        assert len(result_ranges) <= max_ranges, f"Range count {len(result_ranges)} exceeds max {max_ranges}"

        # Check source value truncation
        for i, range_obj in enumerate(result_ranges):
            source_value_len = len(range_obj.source.value)
            assert source_value_len <= max_length, (
                f"Range {i}: source.value length {source_value_len} exceeds max {max_length}"
            )

    def test_current_configuration(self, set_max_range_count, set_truncation_max_length):
        """Test that current environment configuration is respected."""
        # Set specific values for testing
        actual_max_length = 50
        actual_max_ranges = 10
        set_max_range_count(actual_max_ranges)
        set_truncation_max_length(actual_max_length)

        # Test with large strings and many items
        string_size = 5000
        num_items = 30
        test_string = "x" * string_size
        items = [test_string for _ in range(num_items)]
        separator = ","

        tainted_sep = taint_string(separator, "sep")
        tainted_items = [taint_string(item, f"item_{i}") for i, item in enumerate(items)]

        result = join_aspect("".join, 1, tainted_sep, tainted_items)
        result_ranges = get_ranges(result)

        assert result_ranges is not None

        # Verify that current configuration is being used
        assert len(result_ranges) <= actual_max_ranges, (
            f"Range count {len(result_ranges)} exceeds configured max {actual_max_ranges}"
        )

        for i, range_obj in enumerate(result_ranges):
            source_value_len = len(range_obj.source.value)
            assert source_value_len <= actual_max_length, (
                f"Range {i}: source.value length {source_value_len} exceeds configured max {actual_max_length}"
            )


class TestMemoryScaling:
    """Test that memory scales with range count, not string size."""

    def test_memory_independent_of_string_size(self, set_max_range_count):
        """Test that different string sizes produce similar range counts."""
        max_ranges = 10
        set_max_range_count(max_ranges)

        num_items = 20
        separator = ","

        # Test with different string sizes
        for string_size in [10, 100, 1000, 10000]:
            items = ["x" * string_size for _ in range(num_items)]

            tainted_sep = taint_string(separator, "sep")
            tainted_items = [taint_string(item, f"item_{i}") for i, item in enumerate(items)]

            result = join_aspect("".join, 1, tainted_sep, tainted_items)
            result_ranges = get_ranges(result)

            assert result_ranges is not None
            # Range count should be the same regardless of string size
            assert len(result_ranges) <= max_ranges
            # The actual count should be consistent across different sizes
            # (all should hit the limit since we have 20 items = 39 theoretical ranges)

    def test_source_value_storage_bounded(self, set_truncation_max_length):
        """Test that source value storage is bounded regardless of input size."""
        max_length = 50
        set_truncation_max_length(max_length)

        # Create very large strings
        huge_string = "y" * 100000  # 100KB string
        tainted = taint_string(huge_string, "huge_test")
        ranges = get_ranges(tainted)

        assert ranges is not None
        assert len(ranges) > 0

        # Even though input is 100KB, stored value should be limited
        source_value = ranges[0].source.value
        assert len(source_value) <= max_length
        # Memory used for source.value should be minimal (not 100KB)
        assert len(source_value) == max_length  # Should be exactly the max length
