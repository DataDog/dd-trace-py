# -*- coding: utf-8 -*-
"""
Unit tests for the native SpanData class.

These tests focus on edge cases specific to the native/Rust implementation
that wouldn't be caught by testing the higher-level Span class.
"""

import pytest

from ddtrace.internal.native._native import SpanData


# Basic Creation


def test_basic_creation():
    """SpanData can be created with just a name."""
    span = SpanData(name="test.span")
    assert span.name == "test.span"
    assert span.service is None


def test_creation_accepts_extra_kwargs():
    """SpanData accepts extra kwargs without error (for subclass compatibility).

    Since SpanData.__new__ accepts *args/**kwargs, subclasses (like Span) can pass
    additional parameters without needing to override __new__. SpanData only uses
    'name' and 'service'; other parameters are ignored but don't raise errors.
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
        span_api="datadog",
        links=None,
    )
    assert span.name == "test.span"
    assert span.service == "test-service"


# Property Behavior


def test_property_getters_setters():
    """Properties can be read and written."""
    span = SpanData(name="original", service="svc")
    assert span.name == "original"
    assert span.service == "svc"

    span.name = "updated"
    span.service = "new-svc"
    assert span.name == "updated"
    assert span.service == "new-svc"


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


def test_property_independence():
    """Setting name to same value as service doesn't create shared reference."""
    span = SpanData(name="test-name")
    span.service = span.name

    assert span.service == "test-name"
    assert span.name == "test-name"

    # Changing one shouldn't affect the other
    span.service = "different"
    assert span.service == "different"
    assert span.name == "test-name"


# Invalid Input Handling


@pytest.mark.parametrize(
    "invalid_value",
    [
        42,  # integer
        3.14,  # float
        True,  # boolean
        ["list"],  # list
        {"key": "value"},  # dict
        (1, 2, 3),  # tuple
        object(),  # object
    ],
)
def test_name_invalid_types_constructor(invalid_value):
    """Constructor falls back to empty string for invalid name types."""
    span = SpanData(name=invalid_value)
    assert span.name == ""


@pytest.mark.parametrize(
    "invalid_value",
    [
        42,  # integer
        3.14,  # float
        True,  # boolean
        ["list"],  # list
        {"key": "value"},  # dict
        (1, 2, 3),  # tuple
        object(),  # object
    ],
)
def test_name_invalid_types_setter(invalid_value):
    """Setter falls back to empty string for invalid name types."""
    span = SpanData(name="test")
    span.name = invalid_value
    assert span.name == ""


@pytest.mark.parametrize(
    "invalid_value",
    [
        42,
        3.14,
        True,
        ["list"],
        {"key": "value"},
        (1, 2, 3),
        object(),
    ],
)
def test_service_invalid_types_constructor(invalid_value):
    """Constructor falls back to None for invalid service types."""
    span = SpanData(name="test", service=invalid_value)
    assert span.service is None


@pytest.mark.parametrize(
    "invalid_value",
    [
        42,
        3.14,
        True,
        ["list"],
        {"key": "value"},
        (1, 2, 3),
        object(),
    ],
)
def test_service_invalid_types_setter(invalid_value):
    """Setter falls back to None for invalid service types."""
    span = SpanData(name="test", service="valid")
    span.service = invalid_value
    assert span.service is None


# Unicode and Bytes Handling


@pytest.mark.parametrize(
    "unicode_string",
    [
        "test-🔥-span",
        "日本語",
        "中文",
        "한글",
        "العربية",
        "Ελληνικά",
        "emoji-🚀-🎉-✨",
        "mixed-日本語-🔥-test",
    ],
)
def test_unicode_strings(unicode_string):
    """Unicode strings work correctly in name and service."""
    span = SpanData(name=unicode_string, service=unicode_string)
    assert span.name == unicode_string
    assert span.service == unicode_string


@pytest.mark.parametrize(
    "bytes_value",
    [
        b"test-bytes",
        b"hello-world",
        "test-🔥".encode("utf-8"),
        "日本語".encode("utf-8"),
    ],
)
def test_utf8_bytes_constructor(bytes_value):
    """UTF-8 encoded bytes can be used in constructor (preserved as bytes due to zero-copy)."""
    span = SpanData(name=bytes_value, service=bytes_value)
    # PyBackedString preserves the original Python object for zero-copy semantics
    # So bytes input returns bytes output
    assert span.name == bytes_value
    assert span.service == bytes_value


@pytest.mark.parametrize(
    "bytes_value",
    [
        b"test-bytes",
        "test-🔥".encode("utf-8"),
    ],
)
def test_utf8_bytes_setter(bytes_value):
    """UTF-8 encoded bytes can be used in setters (preserved as bytes due to zero-copy)."""
    span = SpanData(name="test")
    span.name = bytes_value
    span.service = bytes_value
    # PyBackedString preserves the original Python object for zero-copy semantics
    # So bytes input returns bytes output
    assert span.name == bytes_value
    assert span.service == bytes_value


@pytest.mark.parametrize(
    "invalid_bytes",
    [
        b"\xff\xfe",  # Invalid UTF-8 sequence
        b"\x80\x81",  # Invalid UTF-8
        b"\xc0\xc1",  # Invalid UTF-8
    ],
)
def test_non_utf8_bytes_constructor(invalid_bytes):
    """Non-UTF-8 bytes fall back to defaults in constructor."""
    span = SpanData(name=invalid_bytes, service=invalid_bytes)
    assert span.name == ""  # name falls back to empty string
    assert span.service is None  # service falls back to None


@pytest.mark.parametrize(
    "invalid_bytes",
    [
        b"\xff\xfe",
        b"\x80\x81",
        b"\xc0\xc1",
    ],
)
def test_non_utf8_bytes_setter(invalid_bytes):
    """Non-UTF-8 bytes fall back to defaults in setters."""
    span = SpanData(name="test", service="test")
    span.name = invalid_bytes
    assert span.name == ""

    span.service = invalid_bytes
    assert span.service is None


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


# Subclassing


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


# Edge Cases


@pytest.mark.parametrize("length", [100, 1000, 10000, 100000])
def test_very_long_strings(length):
    """Very long strings are handled correctly."""
    long_name = "a" * length
    long_service = "b" * length

    span = SpanData(name=long_name, service=long_service)
    assert span.name == long_name
    assert len(span.name) == length
    assert span.service == long_service
    assert len(span.service) == length


@pytest.mark.parametrize(
    "special_string",
    [
        "test\nwith\nnewlines",
        "test\twith\ttabs",
        "test\x00null",
        "test\rwith\rcarriage\rreturns",
        "test\r\nwith\r\nwindows\r\nnewlines",
        "test with    spaces",
        "test\u200bwith\u200bzero\u200bwidth",
    ],
)
def test_special_characters(special_string):
    """Special characters are preserved in strings."""
    span = SpanData(name=special_string, service=special_string)
    assert span.name == special_string
    assert span.service == special_string


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
