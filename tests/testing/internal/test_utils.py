"""Tests for ddtrace.testing.internal.utils module."""

import typing as t
from unittest.mock import Mock

import pytest

from ddtrace.testing.internal import utils
from ddtrace.testing.internal.utils import PlainTestContext
from ddtrace.testing.internal.utils import _gen_item_id
from ddtrace.testing.internal.utils import asbool
from ddtrace.testing.internal.utils import is_file_level_coverage_enabled


class TestGenItemId:
    """Tests for _gen_item_id function."""

    def test_gen_item_id_within_range(self) -> None:
        """Test that _gen_item_id returns a value within the expected range."""
        result = _gen_item_id()
        assert 1 <= result <= (1 << 64) - 1

    def test_gen_item_id_randomness(self) -> None:
        """Test that _gen_item_id returns different values on multiple calls."""
        results = [_gen_item_id() for _ in range(100)]
        # Should have at least some variance (very unlikely to be all the same)
        assert len(set(results)) >= 99


class TestAsbool:
    """Tests for asbool function."""

    def test_asbool_with_none(self) -> None:
        """Test asbool with None returns False."""
        assert asbool(None) is False

    def test_asbool_with_true_bool(self) -> None:
        """Test asbool with True boolean returns True."""
        assert asbool(True) is True

    def test_asbool_with_false_bool(self) -> None:
        """Test asbool with False boolean returns False."""
        assert asbool(False) is False

    def test_asbool_with_true_string(self) -> None:
        """Test asbool with 'true' string returns True."""
        assert asbool("true") is True

    def test_asbool_with_true_string_uppercase(self) -> None:
        """Test asbool with 'TRUE' string returns True."""
        assert asbool("TRUE") is True

    def test_asbool_with_true_string_mixed_case(self) -> None:
        """Test asbool with 'TrUe' string returns True."""
        assert asbool("TrUe") is True

    def test_asbool_with_one_string(self) -> None:
        """Test asbool with '1' string returns True."""
        assert asbool("1") is True

    def test_asbool_with_false_string(self) -> None:
        """Test asbool with 'false' string returns False."""
        assert asbool("false") is False

    def test_asbool_with_zero_string(self) -> None:
        """Test asbool with '0' string returns False."""
        assert asbool("0") is False

    def test_asbool_with_empty_string(self) -> None:
        """Test asbool with empty string returns False."""
        assert asbool("") is False

    def test_asbool_with_arbitrary_string(self) -> None:
        """Test asbool with arbitrary string returns False."""
        assert asbool("hello") is False


class TestIsFileLevelCoverageEnabled:
    @pytest.mark.parametrize(
        "version_info,env_value,expected",
        [
            ((3, 11, 9), None, False),
            ((3, 11, 9), "true", False),
            ((3, 12, 0), None, True),
            ((3, 12, 0), "true", True),
            ((3, 12, 0), "false", False),
            ((3, 14, 2), None, True),
            ((3, 16, 0), None, False),
        ],
    )
    def test_is_file_level_coverage_enabled(
        self,
        monkeypatch: pytest.MonkeyPatch,
        version_info: tuple[int, int, int],
        env_value: t.Optional[str],
        expected: bool,
    ) -> None:
        monkeypatch.setattr(utils, "sys", Mock(version_info=version_info))
        if env_value is None:
            monkeypatch.delenv("_DD_COVERAGE_FILE_LEVEL", raising=False)
        else:
            monkeypatch.setenv("_DD_COVERAGE_FILE_LEVEL", env_value)

        assert is_file_level_coverage_enabled() is expected


class TestPlainTestContext:
    """Tests for PlainTestContext dataclass."""

    def test_test_context_creation(self) -> None:
        """Test that PlainTestContext can be created with span_id and trace_id."""
        span_id = 12345
        trace_id = 67890
        context = PlainTestContext(span_id=span_id, trace_id=trace_id)

        assert context.span_id == span_id
        assert context.trace_id == trace_id
        assert context.get_tags() == {}
        assert context.get_metrics() == {}
