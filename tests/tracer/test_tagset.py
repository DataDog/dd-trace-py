# -*- coding: utf-8 -*-
from collections import OrderedDict

import pytest

from ddtrace.internal._tagset import TagsetDecodeError
from ddtrace.internal._tagset import TagsetEncodeError
from ddtrace.internal._tagset import TagsetMaxSizeEncodeError
from ddtrace.internal._tagset import decode_tagset_string
from ddtrace.internal._tagset import encode_tagset_values
from ddtrace.internal.compat import ensure_str


@pytest.mark.parametrize(
    "header,expected",
    [
        # Normal single tag pair
        ("key=value", {"key": "value"}),
        ("_key=value", {"_key": "value"}),
        # Starting with a digit
        ("1key=value", {"1key": "value"}),
        # Only digits
        ("12345=678910", {"12345": "678910"}),
        # Single character key and value
        ("a=b", {"a": "b"}),
        # Empty value
        ("", {}),
        # Extra trailing comma
        ("key=value,", {"key": "value"}),
        # Values can have spaces
        ("key=value can have spaces", {"key": "value can have spaces"}),
        # Values can have equals
        ("key=value=value", {"key": "value=value"}),
        # Remove leading/trailing spaces from values
        ("key= value can have spaces ", {"key": "value can have spaces"}),
        # Non-space whitespace characters are allowed
        # tenant@vendor format key from tracestate
        ("tenant@vendor=value", {"tenant@vendor": "value"}),
        # Multiple values
        ("a=1,b=2,c=3", {"a": "1", "b": "2", "c": "3"}),
        # Realistic example
        (
            "_dd.p.upstream_services=bWNudWx0eS13ZWI|0|1;dHJhY2Utc3RhdHMtcXVlcnk|2|4,_dd.p.hello=world",
            {"_dd.p.upstream_services": "bWNudWx0eS13ZWI|0|1;dHJhY2Utc3RhdHMtcXVlcnk|2|4", "_dd.p.hello": "world"},
        ),
        (
            "_dd.p.upstream_services=bWNudWx0eS13ZWI===|0|1;dHJhY2Utc3RhdHMtcXVlcnk===|2|4,_dd.p.hello=world",
            {
                "_dd.p.upstream_services": "bWNudWx0eS13ZWI===|0|1;dHJhY2Utc3RhdHMtcXVlcnk===|2|4",
                "_dd.p.hello": "world",
            },
        ),
    ],
)
def test_decode_tagset_string(header, expected):
    """Test that provided header value is parsed as expected"""
    assert expected == decode_tagset_string(header)


@pytest.mark.parametrize(
    "header",
    [
        "key",
        "key=",
        "key=,",
        "=",
        ",",
        ",=,",
        ",=value",
        "=value",
        # Extra leading comma
        ",key=value",
        "key=value,=value",
        "key=value,value",
        # Spaces are not allowed in keys
        "key with spaces=value",
        # Non-space whitespace characters are not allowed in key or value
        "key=value\r\n",
        "key\t=value\r\n",
    ],
)
def test_decode_tagset_string_malformed(header):
    """Test that the provided malformed header values raise an exception"""
    with pytest.raises(TagsetDecodeError):
        decode_tagset_string(header)


@pytest.mark.parametrize(
    "values,expected",
    [
        # No values
        ({}, ""),
        # Single key/value
        ({"key": "value"}, "key=value"),
        # Allow equals in values
        ({"key": "value=with=equals"}, "key=value=with=equals"),
        # Multiple key/values
        # DEV: Use OrderedDict to ensure consistent iteration for encoding
        (OrderedDict([("a", "1"), ("b", "2"), ("c", "3")]), "a=1,b=2,c=3"),
        # Realistic example
        (
            {"_dd.p.upstream_services": "Z3JwYy1jbGllbnQ=|1|0|1.0000"},
            "_dd.p.upstream_services=Z3JwYy1jbGllbnQ=|1|0|1.0000",
        ),
    ],
)
def test_encode_tagset_values(values, expected):
    """Test that we can properly encode data into the expected format"""
    header = encode_tagset_values(values)
    assert expected == header

    # Ensure what we generate also parses correctly
    assert values == decode_tagset_string(header)


def test_encode_tagset_values_strip_spaces():
    """Test that leading and trailing spaces are stripped from keys and values"""
    # Leading/trailing spaces are striped
    values = {" key ": " value "}
    res = encode_tagset_values(values)
    assert "key=value" == res
    assert {"key": "value"} == decode_tagset_string(res)


@pytest.mark.parametrize(
    "values",
    [
        # disallow key with spaces
        {"key with spaces": "value"},
        # disallow commas
        {"key,with,commas": "value"},
        {"key": "value,with,commas"},
        # disallow equals
        {"key=with=equals": "value"},
        # Empty key or value
        {"": "value"},
        {"key": ""},
        # Unicode
        {ensure_str(u"☺️"): "value"},
        {"key": ensure_str(u"☺️")},
    ],
)
def test_encode_tagset_values_malformed(values):
    """Test that invalid data raises a TagsetEncodeError"""
    with pytest.raises(TagsetEncodeError):
        encode_tagset_values(values)


def test_encode_tagset_values_max_size():
    """Test that exceeding the max size raises an exception"""
    # DEV: Use OrderedDict to ensure consistent iteration for encoding
    values = OrderedDict(
        [
            # Short values we know will pack into the final result
            ("a", "1"),
            ("b", "2"),
            ("somereallylongkey", "somereallyreallylongvalue"),
        ]
    )
    with pytest.raises(TagsetMaxSizeEncodeError) as ex_info:
        encode_tagset_values(values, max_size=10)

    ex = ex_info.value
    assert ex.values == values
    assert ex.max_size == 10
    assert ex.current_results == "a=1,b=2"


def test_encode_tagset_values_invalid_type():
    """
    encode_tagset_values accepts `values` as an `object` instead of `dict`
    so we can allow subclasses like `collections.OrderedDict` ensure that
    we properly raise a `TypeError` when a non-dict is passed
    """
    # Allowed
    encode_tagset_values({})
    encode_tagset_values(OrderedDict())

    # Not allowed, AttributeError
    for values in (None, True, 10, object(), []):
        with pytest.raises(AttributeError):
            encode_tagset_values(values)
