# -*- coding: utf-8 -*-
"""
Unit tests for the native SpanData class.

These tests focus on edge cases specific to the native/Rust implementation
that wouldn't be caught by testing the higher-level Span class.
"""

import pytest

from ddtrace.internal.native._native import SpanData


# =============================================================================
# Test Data Constants
# =============================================================================

# Values that are not valid string types - should trigger fallback behavior
INVALID_TYPE_VALUES = [
    pytest.param(42, id="integer"),
    pytest.param(3.14, id="float"),
    pytest.param(True, id="boolean"),
    pytest.param(["list"], id="list"),
    pytest.param({"key": "value"}, id="dict"),
    pytest.param((1, 2, 3), id="tuple"),
    pytest.param(object(), id="object"),
]

# Bytes that are not valid UTF-8 - should trigger fallback behavior
INVALID_UTF8_BYTES = [
    pytest.param(b"\xff\xfe", id="invalid_utf8_fffe"),
    pytest.param(b"\x80\x81", id="invalid_utf8_8081"),
    pytest.param(b"\xc0\xc1", id="invalid_utf8_c0c1"),
]

# Unicode strings that should be preserved exactly
UNICODE_STRINGS = [
    pytest.param("test-🔥-span", id="emoji"),
    pytest.param("日本語", id="japanese"),
    pytest.param("中文", id="chinese"),
    pytest.param("한글", id="korean"),
    pytest.param("العربية", id="arabic"),
    pytest.param("Ελληνικά", id="greek"),
    pytest.param("emoji-🚀-🎉-✨", id="multi_emoji"),
    pytest.param("mixed-日本語-🔥-test", id="mixed"),
]

# Valid UTF-8 bytes - should be preserved (zero-copy semantics)
UTF8_BYTES = [
    pytest.param(b"test-bytes", id="ascii_bytes"),
    pytest.param(b"hello-world", id="hello_bytes"),
    pytest.param("test-🔥".encode("utf-8"), id="emoji_bytes"),
    pytest.param("日本語".encode("utf-8"), id="japanese_bytes"),
]

# Strings with special characters that should be preserved
SPECIAL_CHARACTER_STRINGS = [
    pytest.param("test\nwith\nnewlines", id="newlines"),
    pytest.param("test\twith\ttabs", id="tabs"),
    pytest.param("test\x00null", id="null_byte"),
    pytest.param("test\rwith\rcarriage\rreturns", id="carriage_returns"),
    pytest.param("test\r\nwith\r\nwindows\r\nnewlines", id="windows_newlines"),
    pytest.param("test with    spaces", id="spaces"),
    pytest.param("test\u200bwith\u200bzero\u200bwidth", id="zero_width"),
]

# Property categories for parameterized tests
REQUIRED_STRING_PROPERTIES = ["name", "resource"]
OPTIONAL_STRING_PROPERTIES = ["service", "span_type"]
ALL_STRING_PROPERTIES = REQUIRED_STRING_PROPERTIES + OPTIONAL_STRING_PROPERTIES

# _span_api is special: constructor param is "span_api" but property getter/setter is "_span_api"
# Tests for _span_api are written explicitly rather than parameterized

# Values that are not valid numeric types - should trigger fallback behavior
INVALID_NUMERIC_VALUES = [
    pytest.param("string", id="string"),
    pytest.param(["list"], id="list"),
    pytest.param({"key": "value"}, id="dict"),
    pytest.param(object(), id="object"),
]


# =============================================================================
# Basic Creation Tests
# =============================================================================


def test_basic_creation():
    """SpanData can be created with just a name."""
    span = SpanData(name="test.span")
    assert span.name == "test.span"
    assert span.service is None
    # resource defaults to name when not provided
    assert span.resource == "test.span"
    assert span.span_type is None


def test_creation_accepts_extra_kwargs():
    """SpanData accepts extra kwargs without error (for subclass compatibility).

    Since SpanData.__new__ accepts *args/**kwargs, subclasses (like Span) can pass
    additional parameters without needing to override __new__. SpanData only uses
    'name', 'service', 'resource', 'span_type', and 'span_api'; other parameters are ignored but don't raise errors.
    """
    span = SpanData(
        name="test.span",
        service="test-service",
        resource="test-resource",
        span_type="web",
        trace_id=123,
        span_id=456,
        parent_id=789,
        start=1234567890.0,
        context=None,
        on_finish=None,
        span_api="custom-api",
        links=None,
    )
    assert span.name == "test.span"
    assert span.service == "test-service"
    assert span.resource == "test-resource"
    assert span.span_type == "web"
    assert span._span_api == "custom-api"


# =============================================================================
# Property Behavior Tests
# =============================================================================


def test_property_getters_setters():
    """Properties can be read and written."""
    span = SpanData(name="original", service="svc", resource="res")
    assert span.name == "original"
    assert span.service == "svc"
    assert span.resource == "res"

    span.name = "updated"
    span.service = "new-svc"
    span.resource = "new-res"
    assert span.name == "updated"
    assert span.service == "new-svc"
    assert span.resource == "new-res"


def test_service_none_handling():
    """service can be None and is distinct from empty string."""
    # Default to None
    span = SpanData(name="test")
    assert span.service is None

    # Explicit None
    span = SpanData(name="test", service=None)
    assert span.service is None

    # Can be set to None
    span.service = "service"
    assert span.service == "service"
    span.service = None
    assert span.service is None

    # Empty string is distinct from None
    span.service = ""
    assert span.service == ""
    assert span.service is not None


def test_resource_defaults_to_name():
    """resource defaults to name when not provided."""
    span = SpanData(name="test-name")
    assert span.resource == "test-name"

    # Resource and name should be the same object (zero-copy via clone_ref)
    # This verifies we're using PyBackedString.clone_ref instead of creating a new string
    assert span.name is span.resource

    # Explicitly provided resource overrides the default
    span = SpanData(name="test-name", resource="custom-resource")
    assert span.resource == "custom-resource"
    assert span.name == "test-name"
    # When explicitly provided, they should be different objects
    assert span.name is not span.resource


def test_property_independence():
    """Setting name to same value as service doesn't create shared reference."""
    span = SpanData(name="test-name")
    span.service = span.name

    assert span.service == "test-name"
    assert span.name == "test-name"
    assert span.resource == "test-name"  # resource defaults to name

    # Changing one shouldn't affect the others
    span.service = "different"
    assert span.service == "different"
    assert span.name == "test-name"
    assert span.resource == "test-name"

    # Changing resource doesn't affect name
    span.resource = "different-resource"
    assert span.resource == "different-resource"
    assert span.name == "test-name"


# =============================================================================
# Generic Invalid Type Tests
# =============================================================================


@pytest.mark.parametrize("prop", REQUIRED_STRING_PROPERTIES)
@pytest.mark.parametrize("invalid_value", INVALID_TYPE_VALUES)
def test_required_string_constructor_falls_back_to_empty_string(prop, invalid_value):
    """Required string properties fall back to empty string for invalid types in constructor."""
    props = {"name": "test"}
    props[prop] = invalid_value
    span = SpanData(**props)
    assert getattr(span, prop) == ""


@pytest.mark.parametrize("prop", REQUIRED_STRING_PROPERTIES)
@pytest.mark.parametrize("invalid_value", INVALID_TYPE_VALUES)
def test_required_string_setter_falls_back_to_empty_string(prop, invalid_value):
    """Required string property setters fall back to empty string for invalid types."""
    span = SpanData(name="test", resource="res")
    setattr(span, prop, invalid_value)
    assert getattr(span, prop) == ""


@pytest.mark.parametrize("prop", REQUIRED_STRING_PROPERTIES)
@pytest.mark.parametrize("invalid_bytes", INVALID_UTF8_BYTES)
def test_required_string_constructor_invalid_utf8_falls_back_to_empty_string(prop, invalid_bytes):
    """Required string properties fall back to empty string for invalid UTF-8 bytes."""
    props = {"name": "test"}
    props[prop] = invalid_bytes
    span = SpanData(**props)
    assert getattr(span, prop) == ""


@pytest.mark.parametrize("prop", REQUIRED_STRING_PROPERTIES)
@pytest.mark.parametrize("invalid_bytes", INVALID_UTF8_BYTES)
def test_required_string_setter_invalid_utf8_falls_back_to_empty_string(prop, invalid_bytes):
    """Required string property setters fall back to empty string for invalid UTF-8."""
    span = SpanData(name="test", resource="res")
    setattr(span, prop, invalid_bytes)
    assert getattr(span, prop) == ""


@pytest.mark.parametrize("prop", OPTIONAL_STRING_PROPERTIES)
@pytest.mark.parametrize("invalid_value", INVALID_TYPE_VALUES)
def test_optional_string_constructor_falls_back_to_none(prop, invalid_value):
    """Optional string properties fall back to None for invalid types in constructor."""
    span = SpanData(name="test", **{prop: invalid_value})
    assert getattr(span, prop) is None


@pytest.mark.parametrize("prop", OPTIONAL_STRING_PROPERTIES)
@pytest.mark.parametrize("invalid_value", INVALID_TYPE_VALUES)
def test_optional_string_setter_falls_back_to_none(prop, invalid_value):
    """Optional string property setters fall back to None for invalid types."""
    span = SpanData(name="test", **{prop: "valid"})
    setattr(span, prop, invalid_value)
    assert getattr(span, prop) is None


@pytest.mark.parametrize("prop", OPTIONAL_STRING_PROPERTIES)
@pytest.mark.parametrize("invalid_bytes", INVALID_UTF8_BYTES)
def test_optional_string_constructor_invalid_utf8_falls_back_to_none(prop, invalid_bytes):
    """Optional string properties fall back to None for invalid UTF-8 bytes."""
    span = SpanData(name="test", **{prop: invalid_bytes})
    assert getattr(span, prop) is None


@pytest.mark.parametrize("prop", OPTIONAL_STRING_PROPERTIES)
@pytest.mark.parametrize("invalid_bytes", INVALID_UTF8_BYTES)
def test_optional_string_setter_invalid_utf8_falls_back_to_none(prop, invalid_bytes):
    """Optional string property setters fall back to None for invalid UTF-8."""
    span = SpanData(name="test", **{prop: "valid"})
    setattr(span, prop, invalid_bytes)
    assert getattr(span, prop) is None


# =============================================================================
# Generic Valid String Tests
# =============================================================================


@pytest.mark.parametrize("prop", ALL_STRING_PROPERTIES)
@pytest.mark.parametrize("unicode_string", UNICODE_STRINGS)
def test_unicode_string_constructor_preserves_value(prop, unicode_string):
    """Unicode strings are preserved correctly in constructor."""
    props = {"name": "test"}
    props[prop] = unicode_string
    span = SpanData(**props)
    assert getattr(span, prop) == unicode_string


@pytest.mark.parametrize("prop", ALL_STRING_PROPERTIES)
@pytest.mark.parametrize("unicode_string", UNICODE_STRINGS)
def test_unicode_string_setter_preserves_value(prop, unicode_string):
    """Unicode strings are preserved correctly via setters."""
    span = SpanData(name="test", service="svc", resource="res")
    setattr(span, prop, unicode_string)
    assert getattr(span, prop) == unicode_string


@pytest.mark.parametrize("prop", ALL_STRING_PROPERTIES)
@pytest.mark.parametrize("bytes_value", UTF8_BYTES)
def test_utf8_bytes_constructor_preserves_value(prop, bytes_value):
    """Valid UTF-8 bytes values are preserved in constructor."""
    props = {"name": "test"}
    props[prop] = bytes_value
    span = SpanData(**props)
    assert getattr(span, prop) == bytes_value


@pytest.mark.parametrize("prop", ALL_STRING_PROPERTIES)
@pytest.mark.parametrize("bytes_value", UTF8_BYTES)
def test_utf8_bytes_setter_preserves_value(prop, bytes_value):
    """Valid UTF-8 bytes values are preserved via setters."""
    span = SpanData(name="test", service="svc", resource="res")
    setattr(span, prop, bytes_value)
    assert getattr(span, prop) == bytes_value


@pytest.mark.parametrize("prop", ALL_STRING_PROPERTIES)
@pytest.mark.parametrize("special_string", SPECIAL_CHARACTER_STRINGS)
def test_special_characters_constructor_preserves_value(prop, special_string):
    """Special characters (newlines, tabs, null bytes, etc.) are preserved in constructor."""
    props = {"name": "test"}
    props[prop] = special_string
    span = SpanData(**props)
    assert getattr(span, prop) == special_string


@pytest.mark.parametrize("prop", ALL_STRING_PROPERTIES)
@pytest.mark.parametrize("special_string", SPECIAL_CHARACTER_STRINGS)
def test_special_characters_setter_preserves_value(prop, special_string):
    """Special characters are preserved via setters."""
    span = SpanData(name="test", service="svc", resource="res")
    setattr(span, prop, special_string)
    assert getattr(span, prop) == special_string


@pytest.mark.parametrize("prop", ALL_STRING_PROPERTIES)
@pytest.mark.parametrize("length", [100, 1000, 10000, 100000])
def test_long_strings_constructor_handled(prop, length):
    """Very long strings are handled correctly in constructor."""
    long_string = "x" * length
    props = {"name": "test"}
    props[prop] = long_string
    span = SpanData(**props)
    assert getattr(span, prop) == long_string
    assert len(getattr(span, prop)) == length


@pytest.mark.parametrize("prop", ALL_STRING_PROPERTIES)
@pytest.mark.parametrize("length", [100, 1000, 10000, 100000])
def test_long_strings_setter_handled(prop, length):
    """Very long strings are handled correctly via setters."""
    long_string = "x" * length
    span = SpanData(name="test", service="svc", resource="res")
    setattr(span, prop, long_string)
    assert getattr(span, prop) == long_string
    assert len(getattr(span, prop)) == length


# =============================================================================
# Interning Tests
# =============================================================================


def test_string_interning():
    """Verify that PyBackedString uses Python string interning for static strings."""
    # When we set a property, then read it back, we should get the same object
    # (for interned strings) or at least the same value
    span = SpanData(name="test")

    # Get the name twice - should be the same object if interned
    name1 = span.name
    name2 = span.name

    # At minimum they should be equal
    assert name1 == name2

    # For small identifier-like strings, Python typically interns them
    # so they should be the same object
    assert name1 is name2

    # Setting different fields to the same string value should also use the same interned string
    span.service = "test"
    assert span.name is span.service


def test_rust_static_string_interning():
    """Verify that Rust-created static strings (e.g., defaults) use PyString::intern.

    When PyBackedString has no Python storage (storage: None), as_py() calls
    PyString::intern(py, self.deref()) to create an interned Python string.
    This happens for default/fallback values created by Rust code.
    """
    # Create two spans with invalid name values that fall back to default (empty string)
    # The fallback uses PyBackedString::default() which has storage: None
    span1 = SpanData(name=42)  # Invalid, falls back to ""
    span2 = SpanData(name=["invalid"])  # Invalid, falls back to ""

    # Both should return the same interned empty string object
    name1 = span1.name
    name2 = span2.name

    assert name1 == ""
    assert name2 == ""
    # The key test: both should be the same interned object since they're both
    # Rust static strings returned via PyString::intern
    assert name1 is name2

    # Similarly for multiple reads from the same span
    name3 = span1.name
    assert name1 is name3


# =============================================================================
# Subclassing Tests
# =============================================================================


def test_subclass_override_new():
    """Subclass can override __new__ and call super().__new__()."""

    class CustomSpanData(SpanData):
        def __new__(cls, name, service=None, custom_field=None):
            return super().__new__(cls, name=name, service=service)

    span = CustomSpanData(name="test", service="my-service", custom_field="custom")
    assert span.name == "test"
    assert span.service == "my-service"


def test_subclass_add_slots():
    """Subclass can add __slots__ for additional attributes."""

    class CustomSpanData(SpanData):
        __slots__ = ["custom_attribute"]

    span = CustomSpanData(name="test", service="my-service")
    span.custom_attribute = "custom-value"

    assert span.name == "test"
    assert span.service == "my-service"
    assert span.custom_attribute == "custom-value"


def test_subclass_inherits_properties():
    """Subclass inherits name/service properties correctly."""

    class CustomSpanData(SpanData):
        pass

    span = CustomSpanData(name="test", service="my-service")
    assert span.name == "test"
    assert span.service == "my-service"

    span.name = "updated"
    span.service = "updated-service"
    assert span.name == "updated"
    assert span.service == "updated-service"


# =============================================================================
# Edge Case Tests
# =============================================================================


def test_empty_string_name():
    """Empty string is a valid name."""
    span = SpanData(name="")
    assert span.name == ""


def test_service_none_to_string_transitions():
    """Service can transition between None and string values."""
    span = SpanData(name="test")
    assert span.service is None

    span.service = "service-1"
    assert span.service == "service-1"

    span.service = None
    assert span.service is None

    span.service = "service-2"
    assert span.service == "service-2"


# =============================================================================
# Numeric Property Tests
# =============================================================================


def test_start_ns_getter_setter():
    """start_ns can be read and written."""
    import time

    # start_ns is now set to current time by default (via wall_clock_ns in Rust)
    before = time.time_ns()
    span = SpanData(name="test")
    after = time.time_ns()
    assert before <= span.start_ns <= after

    span.start_ns = 1234567890
    assert span.start_ns == 1234567890

    span.start_ns = 9999999999
    assert span.start_ns == 9999999999


def test_start_ns_accepts_float():
    """start_ns accepts float values (truncated to int)."""
    span = SpanData(name="test")
    span.start_ns = 1234567890.5
    assert span.start_ns == 1234567890

    span.start_ns = 9999999999.9
    assert span.start_ns == 9999999999


@pytest.mark.parametrize("invalid_value", INVALID_NUMERIC_VALUES)
def test_start_ns_invalid_types_fall_back_to_zero(invalid_value):
    """start_ns falls back to 0 for invalid types."""
    span = SpanData(name="test")
    span.start_ns = 100
    assert span.start_ns == 100

    span.start_ns = invalid_value
    assert span.start_ns == 0


def test_duration_ns_getter_setter():
    """duration_ns can be read and written."""
    span = SpanData(name="test")
    assert span.duration_ns is None  # default (sentinel)

    span.duration_ns = 5000000
    assert span.duration_ns == 5000000

    span.duration_ns = 1000000000
    assert span.duration_ns == 1000000000


def test_duration_ns_none_handling():
    """duration_ns can be set to None (stores as 0 sentinel)."""
    span = SpanData(name="test")
    assert span.duration_ns is None

    span.duration_ns = 5000000
    assert span.duration_ns == 5000000

    span.duration_ns = None
    assert span.duration_ns is None


def test_duration_ns_accepts_float():
    """duration_ns accepts float values (truncated to int)."""
    span = SpanData(name="test")
    span.duration_ns = 1234567890.5
    assert span.duration_ns == 1234567890

    span.duration_ns = 9999999999.9
    assert span.duration_ns == 9999999999


@pytest.mark.parametrize("invalid_value", INVALID_NUMERIC_VALUES)
def test_duration_ns_invalid_types_fall_back_to_none(invalid_value):
    """duration_ns falls back to None (0 sentinel) for invalid types."""
    span = SpanData(name="test")
    span.duration_ns = 100
    assert span.duration_ns == 100

    span.duration_ns = invalid_value
    assert span.duration_ns is None


def test_error_getter_setter():
    """error can be read and written."""
    span = SpanData(name="test")
    assert span.error == 0  # default

    span.error = 1
    assert span.error == 1

    span.error = 0
    assert span.error == 0


def test_error_bool_conversion():
    """error accepts bool values (converted to 0/1)."""
    span = SpanData(name="test")
    span.error = True
    assert span.error == 1

    span.error = False
    assert span.error == 0


@pytest.mark.parametrize("invalid_value", INVALID_NUMERIC_VALUES)
def test_error_invalid_types_fall_back_to_zero(invalid_value):
    """error falls back to 0 for invalid types."""
    span = SpanData(name="test")
    span.error = 1
    assert span.error == 1

    span.error = invalid_value
    assert span.error == 0


def test_finished_property():
    """finished property returns True when duration is set, False otherwise."""
    span = SpanData(name="test")
    assert span.finished is False  # duration is None (0 sentinel)

    span.duration_ns = 100
    assert span.finished is True

    span.duration_ns = 1000000000
    assert span.finished is True

    span.duration_ns = None
    assert span.finished is False


def test_finished_is_read_only():
    """finished property is read-only (no setter)."""
    span = SpanData(name="test")
    with pytest.raises(AttributeError):
        span.finished = True


# =============================================================================
# Start Parameter Handling Tests
# =============================================================================


def test_start_ns_default_captures_time():
    """start_ns is set to current time when start not provided."""
    import time

    before = time.time_ns()
    span = SpanData(name="test")
    after = time.time_ns()
    assert before <= span.start_ns <= after


def test_start_ns_from_seconds():
    """start parameter (in seconds) is converted to nanoseconds."""
    span = SpanData(name="test", start=1234567890.5)
    assert span.start_ns == 1234567890500000000


def test_start_ns_from_int_seconds():
    """start parameter accepts integer seconds."""
    span = SpanData(name="test", start=1234567890)
    assert span.start_ns == 1234567890000000000


def test_start_ns_invalid_value_falls_back_to_current_time():
    """start parameter with invalid value falls back to current time."""
    import time

    before = time.time_ns()
    span = SpanData(name="test", start="invalid")
    after = time.time_ns()
    assert before <= span.start_ns <= after


# =============================================================================
# _meta and _metrics Dict Property Tests
# =============================================================================


def test_meta_metrics_default_initialization():
    """_meta and _metrics are initialized as empty dicts."""
    span = SpanData(name="test")
    assert span._meta == {}
    assert span._metrics == {}
    assert isinstance(span._meta, dict)
    assert isinstance(span._metrics, dict)


def test_meta_dict_operations():
    """_meta supports standard dict operations."""
    span = SpanData(name="test")

    # __setitem__
    span._meta["key1"] = "value1"
    assert span._meta["key1"] == "value1"

    # __getitem__
    assert span._meta["key1"] == "value1"

    # __contains__
    assert "key1" in span._meta
    assert "nonexistent" not in span._meta

    # __len__
    assert len(span._meta) == 1

    # .get()
    assert span._meta.get("key1") == "value1"
    assert span._meta.get("nonexistent") is None
    assert span._meta.get("nonexistent", "default") == "default"

    # .items()
    span._meta["key2"] = "value2"
    items = list(span._meta.items())
    assert ("key1", "value1") in items
    assert ("key2", "value2") in items

    # .keys()
    keys = list(span._meta.keys())
    assert "key1" in keys
    assert "key2" in keys

    # .copy()
    meta_copy = span._meta.copy()
    assert meta_copy == {"key1": "value1", "key2": "value2"}
    assert meta_copy is not span._meta

    # .setdefault()
    span._meta.setdefault("key3", "value3")
    assert span._meta["key3"] == "value3"
    span._meta.setdefault("key3", "different")
    assert span._meta["key3"] == "value3"

    # __delitem__
    del span._meta["key1"]
    assert "key1" not in span._meta
    assert len(span._meta) == 2


def test_metrics_dict_operations():
    """_metrics supports standard dict operations."""
    span = SpanData(name="test")

    # __setitem__
    span._metrics["metric1"] = 42
    assert span._metrics["metric1"] == 42

    # __getitem__
    assert span._metrics["metric1"] == 42

    # __contains__
    assert "metric1" in span._metrics
    assert "nonexistent" not in span._metrics

    # __len__
    assert len(span._metrics) == 1

    # .get()
    assert span._metrics.get("metric1") == 42
    assert span._metrics.get("nonexistent") is None
    assert span._metrics.get("nonexistent", 0) == 0

    # .items()
    span._metrics["metric2"] = 3.14
    items = list(span._metrics.items())
    assert ("metric1", 42) in items
    assert ("metric2", 3.14) in items

    # .keys()
    keys = list(span._metrics.keys())
    assert "metric1" in keys
    assert "metric2" in keys

    # .copy()
    metrics_copy = span._metrics.copy()
    assert metrics_copy == {"metric1": 42, "metric2": 3.14}
    assert metrics_copy is not span._metrics

    # .setdefault()
    span._metrics.setdefault("metric3", 100)
    assert span._metrics["metric3"] == 100
    span._metrics.setdefault("metric3", 200)
    assert span._metrics["metric3"] == 100

    # __delitem__
    del span._metrics["metric1"]
    assert "metric1" not in span._metrics
    assert len(span._metrics) == 2


def test_metrics_int_and_float_values():
    """_metrics stores both int and float values correctly."""
    span = SpanData(name="test")

    # Store int values
    span._metrics["int_metric"] = 42
    assert span._metrics["int_metric"] == 42
    assert isinstance(span._metrics["int_metric"], int)

    # Store float values
    span._metrics["float_metric"] = 3.14
    assert span._metrics["float_metric"] == 3.14
    assert isinstance(span._metrics["float_metric"], float)

    # Can update from int to float and vice versa
    span._metrics["int_metric"] = 2.71
    assert span._metrics["int_metric"] == 2.71
    assert isinstance(span._metrics["int_metric"], float)

    span._metrics["float_metric"] = 100
    assert span._metrics["float_metric"] == 100
    assert isinstance(span._metrics["float_metric"], int)


def test_meta_whole_dict_reassignment():
    """_meta can be reassigned to a different dict."""
    span = SpanData(name="test")
    span._meta["original"] = "value"

    new_meta = {"new_key": "new_value"}
    span._meta = new_meta
    assert span._meta == {"new_key": "new_value"}
    assert "original" not in span._meta


def test_metrics_whole_dict_reassignment():
    """_metrics can be reassigned to a different dict."""
    span = SpanData(name="test")
    span._metrics["original"] = 100

    new_metrics = {"new_metric": 200}
    span._metrics = new_metrics
    assert span._metrics == {"new_metric": 200}
    assert "original" not in span._metrics


def test_meta_reference_identity_on_reassignment():
    """Reassigning _meta preserves reference identity."""
    span = SpanData(name="test")

    # Create a new dict and assign it
    d = {"key": "value"}
    span._meta = d

    # The getter should return the same dict
    assert span._meta is d

    # Modifying the original dict should be reflected
    d["new_key"] = "new_value"
    assert span._meta["new_key"] == "new_value"


def test_metrics_reference_identity_on_reassignment():
    """Reassigning _metrics preserves reference identity."""
    span = SpanData(name="test")

    # Create a new dict and assign it
    d = {"metric": 42}
    span._metrics = d

    # The getter should return the same dict
    assert span._metrics is d

    # Modifying the original dict should be reflected
    d["new_metric"] = 100
    assert span._metrics["new_metric"] == 100


def test_meta_dict_sharing_between_instances():
    """Two SpanData instances can share the same _meta dict via assignment."""
    span1 = SpanData(name="span1")
    span2 = SpanData(name="span2")

    # Initially independent
    span1._meta["key1"] = "value1"
    assert "key1" not in span2._meta

    # Share the dict
    span2._meta = span1._meta
    assert span2._meta is span1._meta

    # Modifications in one are reflected in the other
    span1._meta["key2"] = "value2"
    assert span2._meta["key2"] == "value2"

    span2._meta["key3"] = "value3"
    assert span1._meta["key3"] == "value3"


def test_metrics_dict_sharing_between_instances():
    """Two SpanData instances can share the same _metrics dict via assignment."""
    span1 = SpanData(name="span1")
    span2 = SpanData(name="span2")

    # Initially independent
    span1._metrics["metric1"] = 100
    assert "metric1" not in span2._metrics

    # Share the dict
    span2._metrics = span1._metrics
    assert span2._metrics is span1._metrics

    # Modifications in one are reflected in the other
    span1._metrics["metric2"] = 200
    assert span2._metrics["metric2"] == 200

    span2._metrics["metric3"] = 300
    assert span1._metrics["metric3"] == 300


def test_meta_independent_instances():
    """Separate SpanData instances get independent _meta dicts."""
    span1 = SpanData(name="span1")
    span2 = SpanData(name="span2")

    # Dicts should be independent
    assert span1._meta is not span2._meta

    # Modifications to one don't affect the other
    span1._meta["key"] = "value"
    assert "key" not in span2._meta


def test_metrics_independent_instances():
    """Separate SpanData instances get independent _metrics dicts."""
    span1 = SpanData(name="span1")
    span2 = SpanData(name="span2")

    # Dicts should be independent
    assert span1._metrics is not span2._metrics

    # Modifications to one don't affect the other
    span1._metrics["metric"] = 42
    assert "metric" not in span2._metrics


def test_meta_subclass_inheritance():
    """Subclass of SpanData inherits _meta property."""

    class CustomSpanData(SpanData):
        pass

    span = CustomSpanData(name="test")
    assert span._meta == {}

    span._meta["key"] = "value"
    assert span._meta["key"] == "value"

    new_meta = {"custom": "meta"}
    span._meta = new_meta
    assert span._meta == {"custom": "meta"}


def test_metrics_subclass_inheritance():
    """Subclass of SpanData inherits _metrics property."""

    class CustomSpanData(SpanData):
        pass

    span = CustomSpanData(name="test")
    assert span._metrics == {}

    span._metrics["metric"] = 100
    assert span._metrics["metric"] == 100

    new_metrics = {"custom": 200}
    span._metrics = new_metrics
    assert span._metrics == {"custom": 200}


def test_meta_setter_rejects_non_dict():
    """Assigning non-dict to _meta raises TypeError."""
    span = SpanData(name="test")

    with pytest.raises(TypeError):
        span._meta = "not a dict"

    with pytest.raises(TypeError):
        span._meta = 42

    with pytest.raises(TypeError):
        span._meta = ["list"]

    with pytest.raises(TypeError):
        span._meta = None


def test_metrics_setter_rejects_non_dict():
    """Assigning non-dict to _metrics raises TypeError."""
    span = SpanData(name="test")

    with pytest.raises(TypeError):
        span._metrics = "not a dict"

    with pytest.raises(TypeError):
        span._metrics = 42

    with pytest.raises(TypeError):
        span._metrics = ["list"]

    with pytest.raises(TypeError):
        span._metrics = None


def test_meta_getter_returns_same_object():
    """Multiple calls to _meta getter return the same dict object."""
    span = SpanData(name="test")

    meta1 = span._meta
    meta2 = span._meta

    assert meta1 is meta2


def test_metrics_getter_returns_same_object():
    """Multiple calls to _metrics getter return the same dict object."""
    span = SpanData(name="test")

    metrics1 = span._metrics
    metrics2 = span._metrics

    assert metrics1 is metrics2


# =============================================================================
# span_id Tests
# =============================================================================


def test_span_id_basic():
    """span_id can be get and set."""
    span = SpanData(name="test")
    # Default: random u64
    assert isinstance(span.span_id, int)
    assert span.span_id > 0

    # Can set to specific value
    span.span_id = 12345
    assert span.span_id == 12345


def test_span_id_default_random():
    """span_id defaults to random u64 when not provided."""
    span = SpanData(name="test")
    assert isinstance(span.span_id, int)
    assert span.span_id > 0

    # Each span should get a different random ID
    span2 = SpanData(name="test2")
    assert span.span_id != span2.span_id


def test_span_id_none_generates_random():
    """span_id=None generates a random ID."""
    span = SpanData(name="test", span_id=None)
    assert isinstance(span.span_id, int)
    assert span.span_id > 0


def test_span_id_invalid_type_in_constructor():
    """Invalid span_id type in constructor generates random ID instead of raising."""
    span = SpanData(name="test", span_id="invalid")
    assert isinstance(span.span_id, int)
    assert span.span_id > 0


def test_span_id_setter_invalid_type():
    """Setting span_id to invalid type is silently ignored."""
    span = SpanData(name="test", span_id=12345)
    original_id = span.span_id
    assert original_id == 12345

    # Invalid type: should be ignored
    span.span_id = "invalid"
    assert span.span_id == original_id  # Unchanged


def test_span_id_zero():
    """span_id can be set to zero."""
    span = SpanData(name="test", span_id=0)
    assert span.span_id == 0


def test_span_id_max_u64():
    """span_id can be set to max u64 value."""
    max_u64 = (2**64) - 1
    span = SpanData(name="test", span_id=max_u64)
    assert span.span_id == max_u64


def test_span_id_overflow():
    """span_id values larger than u64 are truncated to 64 bits."""
    # Python int larger than u64
    large_value = (2**64) + 123
    span = SpanData(name="test", span_id=large_value)
    # Should truncate to 64 bits (take lower 64 bits)
    # This behavior depends on how PyO3 handles overflow - may wrap or raise
    # For now, just verify it doesn't crash
    assert isinstance(span.span_id, int)


def test_span_id_larger_than_u64_setter():
    """Setting span_id to value larger than u64 max is silently ignored."""
    # This could happen if someone accidentally tries to set span_id = trace_id
    span = SpanData(name="test", span_id=12345)
    original_id = span.span_id
    assert original_id == 12345

    # Try to set to a value larger than u64 max
    larger_than_u64 = (2**64) + 67890
    span.span_id = larger_than_u64

    # Should be silently ignored, keeping the original value
    assert span.span_id == original_id
    assert span.span_id == 12345


# =============================================================================
# _span_api Property Tests
# =============================================================================


def test_span_api_default():
    """_span_api defaults to "datadog" when not provided."""
    span = SpanData(name="test")
    assert span._span_api == "datadog"


def test_span_api_constructor():
    """_span_api can be set via constructor."""
    span = SpanData(name="test", span_api="opentelemetry")
    assert span._span_api == "opentelemetry"


def test_span_api_getter_setter():
    """_span_api can be read and written."""
    span = SpanData(name="test")
    assert span._span_api == "datadog"

    span._span_api = "custom-api"
    assert span._span_api == "custom-api"


@pytest.mark.parametrize("invalid_value", INVALID_TYPE_VALUES)
def test_span_api_invalid_types_fall_back_to_empty_string(invalid_value):
    """_span_api falls back to empty string for invalid types."""
    # Constructor
    span = SpanData(name="test", span_api=invalid_value)
    assert span._span_api == ""

    # Setter
    span = SpanData(name="test")
    span._span_api = invalid_value
    assert span._span_api == ""


@pytest.mark.parametrize("invalid_bytes", INVALID_UTF8_BYTES)
def test_span_api_invalid_utf8_falls_back_to_empty_string(invalid_bytes):
    """_span_api falls back to empty string for invalid UTF-8 bytes."""
    # Constructor
    span = SpanData(name="test", span_api=invalid_bytes)
    assert span._span_api == ""

    # Setter
    span = SpanData(name="test")
    span._span_api = invalid_bytes
    assert span._span_api == ""
