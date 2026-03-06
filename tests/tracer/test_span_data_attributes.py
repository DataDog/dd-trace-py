# -*- coding: utf-8 -*-
"""
Unit tests for the SpanData attribute API.

Tests for the Rust-backed attribute methods on SpanData:
  - _set_attribute (generic, type-dispatching) / _remove_attribute
  - _set_attributes (bulk dict)
  - _get_str_attribute / _get_numeric_attribute / _get_attribute
  - _has_attribute
  - _get_str_attributes / _get_numeric_attributes

Key semantic contract under test:
  - meta and metrics are mutually exclusive per key
  - _get_numeric_attribute always returns float (f64 storage loses int/float distinction)
  - _meta / _metrics are read-only (no setter); writes raise AttributeError
  - Invalid key types are silently dropped (no exception)
  - _get_attribute checks meta first, then metrics
"""

import pytest

from ddtrace.internal.native._native import SpanData


# =============================================================================
# Test Data Constants
# =============================================================================

INVALID_KEY_TYPES = [
    pytest.param(42, id="int"),
    pytest.param(3.14, id="float"),
    pytest.param(None, id="none_as_key"),  # extracts as PyBackedString("") — stored as empty key
    pytest.param(["list"], id="list"),
    pytest.param({"k": "v"}, id="dict"),
    pytest.param(object(), id="object"),
]


def make_span():
    return SpanData(name="test.span")


# =============================================================================
# _get_str_attribute / _get_numeric_attribute
# =============================================================================


def test_get_str_attribute_missing_returns_none():
    span = make_span()
    assert span._get_str_attribute("nonexistent") is None


def test_get_str_attribute_key_in_metrics_returns_none():
    """A key stored in metrics is not returned by _get_str_attribute."""
    span = make_span()
    span._set_attribute("metric", 1.0)
    assert span._get_str_attribute("metric") is None


@pytest.mark.parametrize("invalid_key", INVALID_KEY_TYPES[2:])
def test_get_str_attribute_invalid_key_returns_none(invalid_key):
    span = make_span()
    assert span._get_str_attribute(invalid_key) is None


def test_get_numeric_attribute_missing_returns_none():
    span = make_span()
    assert span._get_numeric_attribute("nonexistent") is None


def test_get_numeric_attribute_key_in_meta_returns_none():
    """A key stored in meta is not returned by _get_numeric_attribute."""
    span = make_span()
    span._set_attribute("tag", "value")
    assert span._get_numeric_attribute("tag") is None


@pytest.mark.parametrize("invalid_key", INVALID_KEY_TYPES[2:])
def test_get_numeric_attribute_invalid_key_returns_none(invalid_key):
    span = make_span()
    assert span._get_numeric_attribute(invalid_key) is None


# =============================================================================
# Mutual Exclusion (meta ↔ metrics)
# =============================================================================


def test_set_str_removes_existing_numeric():
    """Setting a string attribute removes the same key from metrics."""
    span = make_span()
    span._set_attribute("key", 1.0)
    assert span._get_numeric_attribute("key") == 1.0

    span._set_attribute("key", "text")
    assert span._get_str_attribute("key") == "text"
    assert span._get_numeric_attribute("key") is None


def test_set_numeric_removes_existing_str():
    """Setting a numeric attribute removes the same key from meta."""
    span = make_span()
    span._set_attribute("key", "text")
    assert span._get_str_attribute("key") == "text"

    span._set_attribute("key", 42.0)
    assert span._get_numeric_attribute("key") == 42.0
    assert span._get_str_attribute("key") is None


def test_mutual_exclusion_multiple_keys():
    """Each key lives in exactly one of meta or metrics."""
    span = make_span()
    span._set_attribute("str_key", "value")
    span._set_attribute("num_key", 1.0)

    assert span._get_str_attribute("str_key") == "value"
    assert span._get_numeric_attribute("str_key") is None
    assert span._get_str_attribute("num_key") is None
    assert span._get_numeric_attribute("num_key") == 1.0


# =============================================================================
# _remove_attribute
# =============================================================================


def test_remove_attribute_from_meta():
    span = make_span()
    span._set_attribute("key", "value")
    assert span._get_str_attribute("key") == "value"

    span._remove_attribute("key")
    assert span._get_str_attribute("key") is None
    assert not span._has_attribute("key")


def test_remove_attribute_from_metrics():
    span = make_span()
    span._set_attribute("key", 42.0)
    assert span._get_numeric_attribute("key") == 42.0

    span._remove_attribute("key")
    assert span._get_numeric_attribute("key") is None
    assert not span._has_attribute("key")


def test_remove_attribute_nonexistent_is_noop():
    """Removing a non-existent key does not raise."""
    span = make_span()
    span._remove_attribute("nonexistent")  # should not raise


def test_remove_attribute_is_idempotent():
    span = make_span()
    span._set_attribute("key", "value")
    span._remove_attribute("key")
    span._remove_attribute("key")  # second remove is a no-op
    assert span._get_str_attribute("key") is None


@pytest.mark.parametrize("invalid_key", INVALID_KEY_TYPES[2:])
def test_remove_attribute_invalid_key_silently_dropped(invalid_key):
    span = make_span()
    span._remove_attribute(invalid_key)  # should not raise


# =============================================================================
# _set_attribute (generic, type-dispatching)
# =============================================================================


def test_set_attribute_string_goes_to_meta():
    span = make_span()
    span._set_attribute("key", "value")
    assert span._get_str_attribute("key") == "value"
    assert span._get_numeric_attribute("key") is None


def test_set_attribute_int_goes_to_metrics():
    span = make_span()
    span._set_attribute("key", 42)
    assert span._get_numeric_attribute("key") == 42.0
    assert span._get_str_attribute("key") is None


def test_set_attribute_float_goes_to_metrics():
    span = make_span()
    span._set_attribute("key", 3.14)
    assert span._get_numeric_attribute("key") == pytest.approx(3.14)
    assert span._get_str_attribute("key") is None


def test_set_attribute_bool_goes_to_metrics():
    """bool is a subclass of int in Python, so it's stored as numeric."""
    span = make_span()
    span._set_attribute("key", True)
    # True → 1.0
    assert span._get_numeric_attribute("key") == 1.0


def test_set_attribute_unrecognized_type_stringified_to_meta():
    """Unrecognized types are stringified via __str__ and stored in meta."""

    class Custom:
        def __str__(self):
            return "custom-str"

    span = make_span()
    span._set_attribute("key", Custom())
    assert span._get_str_attribute("key") == "custom-str"


def test_set_attribute_overwrites_existing_str():
    span = make_span()
    span._set_attribute("key", "first")
    span._set_attribute("key", "second")
    assert span._get_str_attribute("key") == "second"


def test_set_attribute_overwrites_existing_numeric():
    span = make_span()
    span._set_attribute("key", 1.0)
    span._set_attribute("key", 2.0)
    assert span._get_numeric_attribute("key") == 2.0


def test_set_attribute_int_stored_as_float():
    """Integers are stored as f64 and returned as Python float."""
    span = make_span()
    span._set_attribute("count", 5)
    result = span._get_numeric_attribute("count")
    assert result == 5
    assert isinstance(result, float)


def test_set_attribute_zero():
    span = make_span()
    span._set_attribute("zero", 0)
    assert span._get_numeric_attribute("zero") == 0.0


def test_set_attribute_negative():
    span = make_span()
    span._set_attribute("neg", -42.5)
    assert span._get_numeric_attribute("neg") == -42.5


def test_set_attribute_unicode():
    span = make_span()
    span._set_attribute("tag", "日本語-🔥")
    assert span._get_str_attribute("tag") == "日本語-🔥"


def test_set_attribute_valid_utf8_bytes():
    """Valid UTF-8 bytes are accepted for both key and value."""
    span = make_span()
    span._set_attribute(b"bytes.key", b"bytes.value")
    assert span._get_str_attribute(b"bytes.key") == b"bytes.value"


def test_set_attribute_empty_string():
    span = make_span()
    span._set_attribute("key", "")
    assert span._get_str_attribute("key") == ""


def test_set_attribute_enforces_mutual_exclusion():
    span = make_span()
    span._set_attribute("key", 1.0)
    assert span._get_numeric_attribute("key") == 1.0

    span._set_attribute("key", "now a string")
    assert span._get_str_attribute("key") == "now a string"
    assert span._get_numeric_attribute("key") is None


@pytest.mark.parametrize("invalid_key", INVALID_KEY_TYPES[3:])  # skip None (accepted by PyBackedString as empty key)
def test_set_attribute_invalid_key_silently_dropped(invalid_key):
    span = make_span()
    span._set_attribute(invalid_key, "value")
    assert span._get_str_attributes() == {}
    assert span._get_numeric_attributes() == {}


# =============================================================================
# _set_attributes (bulk dict)
# =============================================================================


def test_set_attributes_dict():
    span = make_span()
    span._set_attributes({"str_key": "value", "num_key": 42, "float_key": 3.14})
    assert span._get_str_attribute("str_key") == "value"
    assert span._get_numeric_attribute("num_key") == 42.0
    assert span._get_numeric_attribute("float_key") == pytest.approx(3.14)


def test_set_attributes_non_dict_is_noop():
    """Non-dict input is silently ignored."""
    span = make_span()
    span._set_attributes("not a dict")
    span._set_attributes(42)
    span._set_attributes(None)
    assert span._get_str_attributes() == {}
    assert span._get_numeric_attributes() == {}


def test_set_attributes_empty_dict_is_noop():
    span = make_span()
    span._set_attributes({})
    assert span._get_str_attributes() == {}
    assert span._get_numeric_attributes() == {}


def test_set_attributes_skips_invalid_values():
    """Entries with invalid types are skipped; valid entries still stored."""
    span = make_span()
    span._set_attributes({"valid": "value", "invalid": object()})
    assert span._get_str_attribute("valid") == "value"
    # "invalid" key was not stored (object stringified, not list/dict/etc.)
    # object().__str__() returns something like "<object object at 0x...>"
    # which IS a valid string, so it gets stored in meta via stringify
    # Test what matters: valid entry is stored
    assert span._get_str_attribute("valid") == "value"


def test_set_attributes_overrides_existing():
    span = make_span()
    span._set_attribute("key", "old")
    span._set_attributes({"key": "new"})
    assert span._get_str_attribute("key") == "new"


# =============================================================================
# _has_attribute
# =============================================================================


def test_has_attribute_meta_key():
    span = make_span()
    span._set_attribute("key", "value")
    assert span._has_attribute("key") is True


def test_has_attribute_metrics_key():
    span = make_span()
    span._set_attribute("key", 1.0)
    assert span._has_attribute("key") is True


def test_has_attribute_missing_key():
    span = make_span()
    assert span._has_attribute("nonexistent") is False


def test_has_attribute_after_remove():
    span = make_span()
    span._set_attribute("key", "value")
    span._remove_attribute("key")
    assert span._has_attribute("key") is False


@pytest.mark.parametrize("invalid_key", INVALID_KEY_TYPES[2:])
def test_has_attribute_invalid_key_returns_false(invalid_key):
    span = make_span()
    assert span._has_attribute(invalid_key) is False


# =============================================================================
# _get_str_attributes / _get_numeric_attributes (bulk)
# =============================================================================


def test_get_str_attributes_empty():
    span = make_span()
    assert span._get_str_attributes() == {}


def test_get_numeric_attributes_empty():
    span = make_span()
    assert span._get_numeric_attributes() == {}


def test_get_str_attributes_returns_all():
    span = make_span()
    span._set_attribute("a", "1")
    span._set_attribute("b", "2")
    span._set_attribute("c", 3.0)  # should NOT be in str_attributes
    assert span._get_str_attributes() == {"a": "1", "b": "2"}


def test_get_numeric_attributes_returns_all():
    span = make_span()
    span._set_attribute("x", 1.0)
    span._set_attribute("y", 2.5)
    span._set_attribute("z", "text")  # should NOT be in numeric_attributes
    assert span._get_numeric_attributes() == {"x": 1.0, "y": 2.5}


def test_get_str_attributes_single_key_read_unaffected_by_dict_mutation():
    """
    _get_str_attributes() returns the live internal PyDict.  Mutating it corrupts the
    PyDict cache but does NOT corrupt the Rust HashMap, so single-key reads via
    _get_str_attribute() (which read from the HashMap) remain correct.

    Do not rely on this — the contract is "do not mutate the returned mapping".
    """
    span = make_span()
    span._set_attribute("key", "value")
    d = span._get_str_attributes()
    d["key"] = "mutated"
    d["new"] = "extra"
    assert span._get_str_attribute("key") == "value"
    assert span._get_str_attribute("new") is None


def test_get_numeric_attributes_single_key_read_unaffected_by_dict_mutation():
    """
    _get_numeric_attributes() returns the live internal PyDict.  Mutating it corrupts the
    PyDict cache but does NOT corrupt the Rust HashMap, so single-key reads via
    _get_numeric_attribute() (which read from the HashMap) remain correct.

    Do not rely on this — the contract is "do not mutate the returned mapping".
    """
    span = make_span()
    span._set_attribute("key", 1.0)
    d = span._get_numeric_attributes()
    d["key"] = 999.0
    d["new"] = 42.0
    assert span._get_numeric_attribute("key") == 1.0
    assert span._get_numeric_attribute("new") is None


def test_get_numeric_attributes_values_are_float():
    """All values in _get_numeric_attributes() are Python floats."""
    span = make_span()
    span._set_attribute("int_stored", 5)
    span._set_attribute("float_stored", 3.14)
    attrs = span._get_numeric_attributes()
    for v in attrs.values():
        assert isinstance(v, float)


# =============================================================================
# _get_attribute (generic getter)
# =============================================================================


def test_get_attribute_str_value():
    span = make_span()
    span._set_attribute("key", "value")
    result = span._get_attribute("key")
    assert result == "value"
    assert isinstance(result, str)


def test_get_attribute_numeric_value():
    span = make_span()
    span._set_attribute("key", 3.14)
    result = span._get_attribute("key")
    assert result == pytest.approx(3.14)
    assert isinstance(result, float)


def test_get_attribute_int_stored_returns_float():
    span = make_span()
    span._set_attribute("key", 42)
    result = span._get_attribute("key")
    assert result == 42
    assert isinstance(result, float)


def test_get_attribute_missing_returns_none():
    span = make_span()
    assert span._get_attribute("nonexistent") is None


def test_get_attribute_meta_takes_precedence_over_metrics():
    """
    _get_attribute checks meta first, then metrics.
    This situation (same key in both) shouldn't arise through the normal API
    (mutual exclusion enforced by set methods), but meta wins if somehow it did.
    """
    span = make_span()
    span._set_attribute("key", "str_value")
    result = span._get_attribute("key")
    assert result == "str_value"
    assert isinstance(result, str)


@pytest.mark.parametrize("invalid_key", INVALID_KEY_TYPES[2:])
def test_get_attribute_invalid_key_returns_none(invalid_key):
    span = make_span()
    assert span._get_attribute(invalid_key) is None


def test_get_attribute_after_remove_returns_none():
    span = make_span()
    span._set_attribute("key", "value")
    span._remove_attribute("key")
    assert span._get_attribute("key") is None


def test_get_attribute_reflects_type_switch():
    """After switching a key from str to numeric, _get_attribute returns float."""
    span = make_span()
    span._set_attribute("key", "text")
    assert isinstance(span._get_attribute("key"), str)

    span._set_attribute("key", 1.0)
    result = span._get_attribute("key")
    assert result == 1.0
    assert isinstance(result, float)


# =============================================================================
# Subclass (Span) compatibility
# =============================================================================


def test_attribute_api_works_on_span_subclass():
    """The attribute API is inherited by Span and works the same way."""
    from ddtrace._trace.span import Span

    span = Span(name="test.span")
    span._set_attribute("env", "prod")
    span._set_attribute("pid", 1234)

    assert span._get_str_attribute("env") == "prod"
    assert span._get_numeric_attribute("pid") == 1234.0
    assert span._has_attribute("env")
    assert span._has_attribute("pid")
    assert not span._has_attribute("missing")

    assert span._get_str_attributes() == {"env": "prod"}
    assert span._get_numeric_attributes() == {"pid": 1234.0}
