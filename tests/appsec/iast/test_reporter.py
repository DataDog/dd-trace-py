import os

import pytest

from ddtrace.appsec._iast.reporter import Evidence
from ddtrace.appsec._iast.reporter import Location
from ddtrace.appsec._iast.reporter import Source
from ddtrace.appsec._iast.reporter import Vulnerability
from ddtrace.appsec._iast.reporter import _truncate_evidence_value
from ddtrace.internal.settings.asm import config as asm_config


def _do_assert_hash(e, f, g, e2):
    assert hash(e) == hash(e2)
    assert hash(e2) != hash(g) and hash(f) != hash(g) and hash(g) != hash(e)


def _do_assert_equality(e, f, g, e2):
    assert e == e2
    assert e2 != g and f != g and g != e


def test_evidence_hash_and_equality():
    e = Evidence(value="SomeEvidenceValue")
    f = Evidence(value="SomeEvidenceValue")
    g = Evidence(value="SomeOtherEvidenceValue")
    e2 = Evidence(value="SomeEvidenceValue")

    _do_assert_hash(e, f, g, e2)
    _do_assert_equality(e, f, g, e2)


def test_evidence_hash_and_equality_valueParts():
    e = Evidence(valueParts=[{"value": "SomeEvidenceValue"}])
    f = Evidence(valueParts=[{"value": "SomeEvidenceValue"}])
    g = Evidence(valueParts=[{"value": "SomeOtherEvidenceValue"}])
    e2 = Evidence(valueParts=[{"value": "SomeEvidenceValue"}])

    _do_assert_hash(e, f, g, e2)
    _do_assert_equality(e, f, g, e2)


def test_location_hash_and_equality():
    e = Location(path="foobar.py", line=35, spanId=123)
    f = Location(path="foobar2.py", line=35, spanId=123)
    g = Location(path="foobar.py", line=36, spanId=123)
    e2 = Location(path="foobar.py", line=35, spanId=123)

    _do_assert_hash(e, f, g, e2)
    _do_assert_equality(e, f, g, e2)


def test_vulnerability_hash_and_equality():
    ev1 = Evidence(value="SomeEvidenceValue")
    ev1bis = Evidence(value="SomeEvidenceValue")
    ev2 = Evidence(value="SomeEvidenceValue")

    loc = Location(path="foobar.py", line=35, spanId=123)

    e = Vulnerability(type="VulnerabilityType", evidence=ev1, location=loc)
    f = Vulnerability(type="VulnerabilityType", evidence=ev2, location=loc)
    g = Vulnerability(type="OtherVulnerabilityType", evidence=ev1, location=loc)
    e2 = Vulnerability(type="VulnerabilityType", evidence=ev1bis, location=loc)

    assert e.hash

    _do_assert_hash(e, f, g, e2)
    _do_assert_equality(e, f, g, e2)


def test_source_hash_and_equality():
    e = Source(origin="SomeOrigin", name="SomeName", value="SomeValue")
    f = Source(origin="SomeOtherOrigin", name="SomeName", value="SomeValue")
    g = Source(origin="SomeOrigin", name="SomeOtherName", value="SomeValue")
    e2 = Source(origin="SomeOrigin", name="SomeName", value="SomeValue")

    _do_assert_hash(e, f, g, e2)
    _do_assert_equality(e, f, g, e2)


class TestEvidenceTruncation:
    """Tests for DD_IAST_TRUNCATION_MAX_VALUE_LENGTH in Evidence values."""

    def test_get_truncation_length_from_config(self):
        """Test getting truncation length from configuration."""
        max_length = asm_config._iast_truncation_max_value_length
        # Should be either the env var value or default (250)
        assert max_length > 0
        assert isinstance(max_length, int)

    @pytest.mark.parametrize(
        "value,expected_length",
        [
            ("short", 5),  # Short string - no truncation
            ("x" * 100, 100),  # Medium string - may or may not be truncated
            ("x" * 500, None),  # Long string - will be truncated to max
        ],
    )
    def test_truncate_evidence_value(self, value, expected_length):
        """Test that _truncate_evidence_value truncates correctly."""
        max_length = asm_config._iast_truncation_max_value_length

        result = _truncate_evidence_value(value)

        assert result is not None
        assert len(result) <= max_length

        if expected_length is None:
            # Long string - should be truncated to max
            assert len(result) == max_length
        else:
            # Short/medium string - should be preserved or truncated to max
            assert len(result) == min(expected_length, max_length)

    def test_truncate_none_value(self):
        """Test that None values remain None."""
        result = _truncate_evidence_value(None)
        assert result is None

    def test_truncate_empty_string(self):
        """Test that empty strings remain empty."""
        result = _truncate_evidence_value("")
        assert result == ""

    @pytest.mark.parametrize(
        "string_length",
        [
            1,
            10,
            50,
            100,
            250,
            500,
            1000,
            10000,
        ],
    )
    def test_truncation_preserves_prefix(self, string_length):
        """Test that truncation preserves the beginning of the string."""
        max_length = asm_config._iast_truncation_max_value_length

        original = "x" * string_length
        truncated = _truncate_evidence_value(original)

        assert truncated is not None

        # Truncated value should match the prefix of original
        expected_length = min(string_length, max_length)
        assert truncated == original[:expected_length]

    def test_truncation_with_unicode(self):
        """Test truncation with unicode characters."""
        max_length = asm_config._iast_truncation_max_value_length

        # Mix of ASCII and unicode
        unicode_string = "Hello 🌍 World " * 50
        truncated = _truncate_evidence_value(unicode_string)

        assert truncated is not None
        assert len(truncated) <= max_length
        # Should preserve the beginning
        assert truncated == unicode_string[: len(truncated)]

    def test_default_truncation_length(self):
        """Test that default truncation length is 250 when env var not set."""
        # Get the current max length
        max_length = asm_config._iast_truncation_max_value_length

        # Default should be 250 unless overridden by env var
        env_value = os.environ.get("DD_IAST_TRUNCATION_MAX_VALUE_LENGTH")
        if env_value:
            assert max_length == int(env_value)
        else:
            assert max_length == 250
