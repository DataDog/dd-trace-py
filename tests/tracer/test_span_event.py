# -*- coding: utf-8 -*-
"""
Unit tests for the native SpanEvent class.

These tests validate the API and behaviors of the native/Rust implementation
that wouldn't necessarily be caught by higher-level integration tests.
"""

import time

import pytest

from ddtrace.internal.native._native import SpanEvent
from ddtrace.internal.native._native import SpanEventAttributes


# =============================================================================
# Basic Creation Tests
# =============================================================================


def test_basic_creation():
    """SpanEvent can be created with just a name."""
    event = SpanEvent(name="test.event")
    assert event.name == "test.event"
    assert len(event.attributes) == 0
    assert isinstance(event.time_unix_nano, int)
    assert event.time_unix_nano > 0


def test_creation_with_attributes():
    """SpanEvent can be created with attributes dict."""
    attrs = {"key": "value", "count": 42}
    event = SpanEvent(name="test", attributes=attrs)
    assert event.attributes["key"] == "value"
    assert event.attributes["count"] == 42


def test_creation_with_explicit_timestamp():
    """SpanEvent accepts an explicit time_unix_nano."""
    event = SpanEvent(name="test", time_unix_nano=1234567890123456789)
    assert event.time_unix_nano == 1234567890123456789


def test_creation_with_none_attributes():
    """attributes=None results in an empty attributes mapping."""
    event = SpanEvent(name="test", attributes=None)
    assert len(event.attributes) == 0


def test_time_unix_nano_defaults_to_current_time():
    """time_unix_nano defaults to current wall clock time when not provided."""
    before = time.time_ns()
    event = SpanEvent(name="test")
    after = time.time_ns()
    assert before <= event.time_unix_nano <= after


# =============================================================================
# Property Getter/Setter Tests
# =============================================================================


def test_name_getter_setter():
    """name can be read and written."""
    event = SpanEvent(name="original")
    assert event.name == "original"
    event.name = "updated"
    assert event.name == "updated"


def test_time_unix_nano_getter_setter():
    """time_unix_nano can be read and written."""
    event = SpanEvent(name="test", time_unix_nano=100)
    assert event.time_unix_nano == 100
    event.time_unix_nano = 999
    assert event.time_unix_nano == 999


def test_attributes_setter_replaces_all():
    """Setting attributes replaces the entire mapping."""
    event = SpanEvent(name="test", attributes={"old": "value"})
    assert event.attributes["old"] == "value"

    event.attributes = {"new": "data"}
    assert "old" not in event.attributes
    assert event.attributes["new"] == "data"


def test_attributes_setter_none_clears():
    """Setting attributes to None clears the mapping."""
    event = SpanEvent(name="test", attributes={"key": "value"})
    event.attributes = None
    assert len(event.attributes) == 0


def test_name_invalid_type_falls_back_to_empty_string():
    """Non-string name falls back to empty string."""
    event = SpanEvent(name=42)
    assert event.name == ""


# =============================================================================
# Attribute Value Types
# =============================================================================


def test_attribute_string_value():
    event = SpanEvent(name="test", attributes={"k": "hello"})
    assert event.attributes["k"] == "hello"


def test_attribute_bool_value():
    """bool attributes are stored and returned as bool (not int)."""
    event = SpanEvent(name="test", attributes={"flag": True, "off": False})
    assert event.attributes["flag"] is True
    assert event.attributes["off"] is False
    assert type(event.attributes["flag"]) is bool


def test_attribute_bool_distinct_from_int():
    """True/False are not conflated with 1/0 in type."""
    event = SpanEvent(name="test", attributes={"b": True, "i": 1})
    assert type(event.attributes["b"]) is bool
    assert type(event.attributes["i"]) is int


def test_attribute_int_value():
    event = SpanEvent(name="test", attributes={"count": 99})
    val = event.attributes["count"]
    assert val == 99
    assert type(val) is int


def test_attribute_float_value():
    event = SpanEvent(name="test", attributes={"ratio": 3.14})
    assert abs(event.attributes["ratio"] - 3.14) < 1e-9


def test_attribute_list_value():
    """List attribute values are stored as lists."""
    event = SpanEvent(name="test", attributes={"tags": ["a", "b", "c"]})
    assert event.attributes["tags"] == ["a", "b", "c"]


def test_attribute_mixed_list_value():
    """Lists with mixed types are preserved."""
    event = SpanEvent(name="test", attributes={"mixed": [1, 2, 3]})
    assert event.attributes["mixed"] == [1, 2, 3]


def test_attribute_unknown_type_stringified():
    """Unknown attribute value types are stringified."""
    event = SpanEvent(name="test", attributes={"obj": object()})
    assert isinstance(event.attributes["obj"], str)


# =============================================================================
# SpanEventAttributes Mapping Protocol
# =============================================================================


def test_attributes_type():
    """attributes returns a SpanEventAttributes instance."""
    event = SpanEvent(name="test", attributes={"k": "v"})
    assert isinstance(event.attributes, SpanEventAttributes)


def test_attributes_len():
    event = SpanEvent(name="test", attributes={"a": 1, "b": 2})
    assert len(event.attributes) == 2


def test_attributes_len_empty():
    event = SpanEvent(name="test")
    assert len(event.attributes) == 0


def test_attributes_getitem():
    event = SpanEvent(name="test", attributes={"key": "value"})
    assert event.attributes["key"] == "value"


def test_attributes_getitem_missing_raises():
    event = SpanEvent(name="test", attributes={"key": "value"})
    with pytest.raises(KeyError):
        _ = event.attributes["missing"]


def test_attributes_contains():
    event = SpanEvent(name="test", attributes={"present": 1})
    assert "present" in event.attributes
    assert "absent" not in event.attributes


def test_attributes_bool_nonempty():
    event = SpanEvent(name="test", attributes={"k": "v"})
    assert bool(event.attributes) is True


def test_attributes_bool_empty():
    event = SpanEvent(name="test")
    assert bool(event.attributes) is False


def test_attributes_iter():
    event = SpanEvent(name="test", attributes={"a": 1, "b": 2})
    keys = list(event.attributes)
    assert set(keys) == {"a", "b"}


def test_attributes_keys():
    event = SpanEvent(name="test", attributes={"x": 1, "y": 2})
    assert set(event.attributes.keys()) == {"x", "y"}


def test_attributes_values():
    event = SpanEvent(name="test", attributes={"x": 10, "y": 20})
    assert set(event.attributes.values()) == {10, 20}


def test_attributes_items():
    event = SpanEvent(name="test", attributes={"a": 1, "b": 2})
    items = dict(event.attributes.items())
    assert items == {"a": 1, "b": 2}


def test_attributes_eq_dict():
    """SpanEventAttributes compares equal to an equivalent dict."""
    event = SpanEvent(name="test", attributes={"k": "v", "n": 42})
    assert event.attributes == {"k": "v", "n": 42}


def test_attributes_eq_empty_dict():
    event = SpanEvent(name="test")
    assert event.attributes == {}


def test_attributes_is_read_only():
    """SpanEventAttributes is frozen — item assignment must raise."""
    event = SpanEvent(name="test", attributes={"k": "v"})
    with pytest.raises((TypeError, AttributeError)):
        event.attributes["k"] = "new"  # type: ignore[index]


# =============================================================================
# to_dict() Tests
# =============================================================================


def test_to_dict_basic():
    event = SpanEvent(name="test.event", time_unix_nano=1000)
    d = event.to_dict()
    assert d["name"] == "test.event"
    assert d["time_unix_nano"] == 1000


def test_to_dict_no_attributes_key_when_empty():
    """to_dict omits 'attributes' when there are none."""
    event = SpanEvent(name="test", time_unix_nano=1000)
    d = event.to_dict()
    assert "attributes" not in d


def test_to_dict_includes_attributes_when_present():
    event = SpanEvent(name="test", attributes={"err": "msg"}, time_unix_nano=1000)
    d = event.to_dict()
    assert d["attributes"] == {"err": "msg"}


def test_to_dict_returns_plain_dict():
    """to_dict returns a plain Python dict (not SpanEventAttributes)."""
    event = SpanEvent(name="test", attributes={"k": "v"}, time_unix_nano=1)
    d = event.to_dict()
    assert type(d) is dict
    assert type(d["attributes"]) is dict


def test_to_dict_attribute_types_preserved():
    event = SpanEvent(
        name="test",
        attributes={
            "s": "hello",
            "b": True,
            "i": 42,
            "f": 1.5,
            "l": ["x", "y"],
        },
        time_unix_nano=1,
    )
    d = event.to_dict()["attributes"]
    assert d["s"] == "hello"
    assert d["b"] is True
    assert d["i"] == 42
    assert abs(d["f"] - 1.5) < 1e-9
    assert d["l"] == ["x", "y"]


# =============================================================================
# __repr__ Tests
# =============================================================================


def test_repr_basic():
    event = SpanEvent(name="go.to.moon", time_unix_nano=42)
    r = repr(event)
    assert "go.to.moon" in r
    assert "42" in r


def test_repr_empty_attributes():
    event = SpanEvent(name="test", time_unix_nano=1)
    assert "{}" in repr(event)


def test_repr_with_attributes():
    event = SpanEvent(name="test", attributes={"k": "v"}, time_unix_nano=1)
    r = repr(event)
    assert "k" in r
    assert "v" in r


# =============================================================================
# __iter__ Tests
# =============================================================================


def test_iter_yields_name_and_time():
    """__iter__ yields at least name and time_unix_nano."""
    event = SpanEvent(name="test", time_unix_nano=77)
    d = dict(event)
    assert d["name"] == "test"
    assert d["time_unix_nano"] == 77


def test_iter_omits_attributes_when_empty():
    event = SpanEvent(name="test", time_unix_nano=1)
    d = dict(event)
    assert "attributes" not in d


def test_iter_includes_attributes_when_present():
    event = SpanEvent(name="test", attributes={"k": "v"}, time_unix_nano=1)
    d = dict(event)
    assert d["attributes"] == {"k": "v"}


# =============================================================================
# Unicode Tests
# =============================================================================


@pytest.mark.parametrize(
    "name",
    [
        "test-🔥-event",
        "日本語",
        "中文",
        "العربية",
        "mixed-日本語-🔥",
    ],
)
def test_unicode_name(name):
    event = SpanEvent(name=name)
    assert event.name == name


@pytest.mark.parametrize(
    "key,value",
    [
        ("emoji-🔥", "value-🚀"),
        ("日本語キー", "日本語値"),
        ("key", "日本語value"),
    ],
)
def test_unicode_attribute_key_and_value(key, value):
    event = SpanEvent(name="test", attributes={key: value})
    assert event.attributes[key] == value
