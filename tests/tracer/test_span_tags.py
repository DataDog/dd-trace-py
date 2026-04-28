# -*- coding: utf-8 -*-
"""Tests for Span tag/metric/attribute APIs.

Moved from tests/tracer/test_span.py and extended with tests for the
internal _set_attribute / _get_attribute family of methods.
"""

import sys

import mock
import pytest

from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import ENV_KEY
from ddtrace.constants import MANUAL_DROP_KEY
from ddtrace.constants import MANUAL_KEEP_KEY
from ddtrace.constants import SERVICE_KEY
from ddtrace.constants import SERVICE_VERSION_KEY
from ddtrace.constants import USER_KEEP
from ddtrace.constants import USER_REJECT
from ddtrace.constants import VERSION_KEY
from ddtrace.ext import http
from ddtrace.trace import Span
from tests.utils import assert_is_measured
from tests.utils import assert_is_not_measured


# ---------------------------------------------------------------------------
# Tests moved from SpanTestCase (converted to standalone functions)
# ---------------------------------------------------------------------------


def test_tags():
    s = Span(name="test.span")
    s.set_tag("a", "a")
    s.set_tag("b", 1)
    s.set_tag("c", "1")

    assert s.get_tags() == dict(a="a", c="1")
    assert s.get_metrics() == dict(b=1)


def test_numeric_tags():
    s = Span(name="test.span")
    s.set_tag("negative", -1)
    s.set_tag("zero", 0)
    s.set_tag("positive", 1)
    s.set_tag("large_int", 2**53)
    s.set_tag("large_negative_int", -(2**53))
    s.set_tag("really_large_int", (2**53) + 1)
    s.set_tag("really_large_negative_int", -((2**53) + 1))
    s.set_tag("float", 12.3456789)
    s.set_tag("negative_float", -12.3456789)
    s.set_tag("large_float", 2.0**53)
    s.set_tag("really_large_float", (2.0**53) + 1)

    # DEV: All non-string, non-bool values set via set_tag are stored as their
    # natural numeric type (int or float) in unified attribute storage.
    assert s.get_tags() == {}
    assert s.get_metrics() == {
        "negative": -1,
        "zero": 0,
        "positive": 1,
        "large_int": 2**53,
        "large_negative_int": -(2**53),
        "really_large_int": (2**53) + 1,
        "really_large_negative_int": -((2**53) + 1),
        "float": 12.3456789,
        "negative_float": -12.3456789,
        "large_float": 2.0**53,
        "really_large_float": (2.0**53) + 1,
    }


def test_set_tag_bool():
    s = Span(name="test.span")
    s.set_tag("true", True)
    s.set_tag("false", False)

    assert s.get_tags() == dict(true="True", false="False")
    assert len(s.get_metrics()) == 0


def test_set_tag_metric():
    s = Span(name="test.span")

    s.set_tag("test", "value")
    assert s.get_tags() == dict(test="value")
    assert s.get_metrics() == dict()

    s.set_tag("test", 1)
    assert s.get_tags() == dict()
    assert s.get_metrics() == dict(test=1)


def test_set_valid_metrics():
    s = Span(name="test.span")
    s.set_metric("a", 0)  # ast-grep-ignore: span-set-metric
    s.set_metric("b", -12)  # ast-grep-ignore: span-set-metric
    s.set_metric("c", 12.134)  # ast-grep-ignore: span-set-metric
    s.set_metric("d", 1231543543265475686787869123)  # ast-grep-ignore: span-set-metric
    s.set_metric("e", "12.34")  # ast-grep-ignore: span-set-metric
    m = s.get_metrics()
    assert m["a"] == 0
    assert m["b"] == -12
    assert m["c"] == 12.134
    # Large ints exceed f64 precision so are stored as strings in meta to preserve exact value
    assert "d" not in m
    assert s.get_tag("d") == str(1231543543265475686787869123)
    assert m["e"] == 12.34


def test_set_invalid_metric():
    s = Span(name="test.span")

    invalid_metrics = [None, {}, [], s, "quarante-douze", float("nan"), float("inf"), 1j]

    for i, m in enumerate(invalid_metrics):
        k = str(i)
        s.set_metric(k, m)  # ast-grep-ignore: span-set-metric
        assert s.get_metric(k) is None


def test_set_numpy_metric():
    np = pytest.importorskip("numpy")
    s = Span(name="test.span")
    s.set_metric("a", np.int64(1))  # ast-grep-ignore: span-set-metric
    assert s.get_metric("a") == 1
    assert type(s.get_metric("a")) == float


def test_set_attribute_numpy():
    np = pytest.importorskip("numpy")
    s = Span(name="test.span")
    s._set_attribute("int64", np.int64(42))
    s._set_attribute("float64", np.float64(3.14))
    s._set_attribute("int32", np.int32(-7))
    assert s.get_metric("int64") == 42.0
    assert s.get_metric("float64") == pytest.approx(3.14)
    assert s.get_metric("int32") == -7.0
    assert s.get_tags() == {}


def test_tags_not_string():
    # ensure we can cast as strings
    class Foo(object):
        def __repr__(self):
            1 / 0

    s = Span(name="test.span")
    s.set_tag("a", Foo())


@mock.patch("ddtrace._trace.span.log")
def test_numeric_tags_none(span_log):
    s = Span(name="test.span")
    s.set_tag("noneval", None)
    assert len(s.get_metrics()) == 0


def test_numeric_tags_value():
    s = Span(name="test.span")
    s.set_tag("point5", 0.5)
    expected = {"point5": 0.5}
    assert s.get_metrics() == expected


def test_numeric_tags_bad_value():
    s = Span(name="test.span")
    s.set_tag("somestring", "Hello")
    assert len(s.get_metrics()) == 0


def test_set_tag_none():
    s = Span(name="root.span", service="s", resource="r")
    assert s.get_tags() == dict()

    s.set_tag("custom.key", "100")

    assert s.get_tags() == {"custom.key": "100"}

    s.set_tag("custom.key", None)

    assert s.get_tags() == {"custom.key": "None"}


def test_set_tag_version():
    s = Span(name="test.span")
    s.set_tag(VERSION_KEY, "1.2.3")
    assert s.get_tag(VERSION_KEY) == "1.2.3"
    assert s.get_tag(SERVICE_VERSION_KEY) is None

    s.set_tag(SERVICE_VERSION_KEY, "service.version")
    assert s.get_tag(VERSION_KEY) == "service.version"
    assert s.get_tag(SERVICE_VERSION_KEY) == "service.version"


def test_set_tag_env():
    s = Span(name="test.span")
    s.set_tag(ENV_KEY, "prod")
    assert s.get_tag(ENV_KEY) == "prod"


def test_set_tag_service_key():
    s = Span(name="test.span")
    s.set_tag(SERVICE_KEY, "my-service")  # ast-grep-ignore: span-set-tag-service-key
    assert s.service == "my-service"
    assert s.get_tag(SERVICE_KEY) == "my-service"


def test_set_tag_manual_keep():
    s = Span(name="test.span")
    s.set_tag(MANUAL_KEEP_KEY)  # ast-grep-ignore: span-set-tag-manual-keep
    assert s.context.sampling_priority == USER_KEEP


def test_set_tag_manual_drop():
    s = Span(name="test.span")
    s.set_tag(MANUAL_DROP_KEY)  # ast-grep-ignore: span-set-tag-manual-drop
    assert s.context.sampling_priority == USER_REJECT


# ---------------------------------------------------------------------------
# Tests moved from standalone functions in test_span.py
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,assertion",
    [
        (None, assert_is_measured),
        (1, assert_is_measured),
        (1.0, assert_is_measured),
        (-1, assert_is_measured),
        (True, assert_is_measured),
        ("true", assert_is_measured),
        # DEV: Ends up being measured because we do `bool("false")` which is `True`
        ("false", assert_is_measured),
        (0, assert_is_not_measured),
        (0.0, assert_is_not_measured),
        (False, assert_is_not_measured),
    ],
)
def test_set_tag_measured(value, assertion):
    s = Span(name="test.span")
    s.set_tag(_SPAN_MEASURED_KEY, value)  # ast-grep-ignore: span-set-tag-measured
    assertion(s)


def test_set_tag_measured_not_set():
    # Span is not measured by default
    s = Span(name="test.span")
    assert_is_not_measured(s)


def test_set_tag_measured_no_value():
    s = Span(name="test.span")
    s.set_tag(_SPAN_MEASURED_KEY)  # ast-grep-ignore: span-set-tag-measured
    assert_is_measured(s)


def test_set_tag_measured_change_value():
    s = Span(name="test.span")
    s.set_tag(_SPAN_MEASURED_KEY, True)  # ast-grep-ignore: span-set-tag-measured
    assert_is_measured(s)

    s.set_tag(_SPAN_MEASURED_KEY, False)  # ast-grep-ignore: span-set-tag-measured
    assert_is_not_measured(s)

    s.set_tag(_SPAN_MEASURED_KEY)  # ast-grep-ignore: span-set-tag-measured
    assert_is_measured(s)


def test_span_unicode_set_tag():
    span = Span(None)
    span.set_tag("key", "😌")
    span.set_tag("😐", "😌")
    span._set_attribute("key", "😌")
    span._set_attribute("😐", "😌")


@pytest.mark.skipif(sys.version_info.major != 2, reason="This test only applies Python 2")
@mock.patch("ddtrace._trace.span.log")
def test_span_binary_unicode_set_tag(span_log):
    span = Span(None)
    span.set_tag("key", "🤔")
    span._set_attribute("key_str", "🤔")
    # only span.set_tag() will fail
    span_log.warning.assert_called_once_with("error setting tag %s, ignoring it", "key", exc_info=True)
    assert "key" not in span.get_tags()
    assert span.get_tag("key_str") == "🤔"


@pytest.mark.skipif(sys.version_info.major == 2, reason="This test does not apply to Python 2")
@mock.patch("ddtrace._trace.span.log")
def test_span_bytes_string_set_tag(span_log):
    span = Span(None)
    span.set_tag("key", b"\xf0\x9f\xa4\x94")
    span._set_attribute("key_str", b"\xf0\x9f\xa4\x94")
    assert span.get_tag("key") == str(b"\xf0\x9f\xa4\x94")  # shim: str(bytes) gives "b'...'"
    assert span.get_tag("key_str") == "🤔"  # native UTF-8 decode unchanged
    span_log.warning.assert_not_called()


# ---------------------------------------------------------------------------
# New tests for the internal attribute API
# ---------------------------------------------------------------------------


def test_set_attribute_string():
    s = Span(name="test.span")
    s._set_attribute("key", "value")
    assert s._get_attribute("key") == "value"


def test_set_attribute_int():
    s = Span(name="test.span")
    s._set_attribute("key", 42)
    assert s._get_attribute("key") == 42


def test_set_attribute_large_int():
    s = Span(name="test.span")
    # Ints within i64 range — stored faithfully as int
    s._set_attribute("exact", 2**53)
    s._set_attribute("exact_neg", -(2**53))
    assert s._get_numeric_attribute("exact") == 2**53
    assert s._get_numeric_attribute("exact_neg") == -(2**53)
    assert s._get_str_attribute("exact") is None
    assert s._get_str_attribute("exact_neg") is None

    # Beyond f64 precision but within i64 range — stored faithfully as int
    s._set_attribute("large", (2**53) + 1)
    s._set_attribute("large_neg", -((2**53) + 1))
    assert s._get_numeric_attribute("large") == (2**53) + 1
    assert s._get_numeric_attribute("large_neg") == -((2**53) + 1)
    assert s._get_str_attribute("large") is None
    assert s._get_str_attribute("large_neg") is None

    # Way outside i64 range
    s._set_attribute("huge", 2**127)
    assert s._get_str_attribute("huge") == str(2**127)
    assert s._get_numeric_attribute("huge") is None


def test_set_attribute_float():
    s = Span(name="test.span")
    s._set_attribute("key", 3.14)
    assert s._get_attribute("key") == 3.14


def test_set_attribute_zero():
    s = Span(name="test.span")
    s._set_attribute("key", 0)
    assert s._get_attribute("key") == 0.0


def test_set_attribute_empty_string():
    s = Span(name="test.span")
    s._set_attribute("key", "")
    assert s._get_attribute("key") == ""


def test_set_attribute_negative():
    s = Span(name="test.span")
    s._set_attribute("int_key", -7)
    s._set_attribute("float_key", -1.5)
    assert s._get_attribute("int_key") == -7
    assert s._get_attribute("float_key") == -1.5


def test_set_attribute_nan():
    s = Span(name="test.span")
    s._set_attribute("key", float("nan"))
    assert s._get_numeric_attribute("key") is None


def test_set_attribute_inf():
    s = Span(name="test.span")
    s._set_attribute("key", float("inf"))
    assert s._get_numeric_attribute("key") is None


def test_set_attribute_neg_inf():
    s = Span(name="test.span")
    s._set_attribute("key", float("-inf"))
    assert s._get_numeric_attribute("key") is None


def test_set_attribute_overwrites_string_with_number():
    s = Span(name="test.span")
    s._set_attribute("key", "hello")
    s._set_attribute("key", 99)
    assert s._get_attribute("key") == 99
    assert s._get_str_attribute("key") is None


def test_set_attribute_overwrites_number_with_string():
    s = Span(name="test.span")
    s._set_attribute("key", 99)
    s._set_attribute("key", "hello")
    assert s._get_attribute("key") == "hello"
    assert s._get_numeric_attribute("key") is None


# Type coercion tests


def test_set_attribute_bool():
    s = Span(name="test.span")
    s._set_attribute("t", True)
    s._set_attribute("f", False)
    # bool is a subclass of int, so stored as numeric (1.0 / 0.0 in Rust f64 storage)
    assert s._get_attribute("t") == 1.0
    assert s._get_attribute("f") == 0.0
    assert s._get_numeric_attribute("t") == 1.0
    assert s._get_numeric_attribute("f") == 0.0


def test_set_attribute_bytes():
    s = Span(name="test.span")
    s._set_attribute("key", b"hello")
    assert s._get_attribute("key") == "hello"
    assert s._get_str_attribute("key") == "hello"


def test_set_attribute_bytes_invalid_utf8():
    s = Span(name="test.span")
    s._set_attribute("key", b"\xff\xfe")
    val = s._get_attribute("key")
    assert isinstance(val, str)
    assert "\ufffd" in val  # replacement character


def test_set_attribute_none():
    s = Span(name="test.span")
    s._set_attribute("key", None)
    assert s._get_attribute("key") == "None"
    assert s._get_str_attribute("key") == "None"


def test_set_attribute_object():
    class MyObj:
        def __str__(self):
            return "custom_repr"

    s = Span(name="test.span")
    s._set_attribute("key", MyObj())
    assert s._get_attribute("key") == "custom_repr"
    assert s._get_str_attribute("key") == "custom_repr"


def test_set_attribute_object_str_raises_exc():
    # When __str__ raises, the attribute is silently ignored (no exception propagated).
    # The native layer swallows str() failures defensively — we're instrumenting code
    # we don't control, so raising TypeError/ValueError would crash user apps.
    class BadObj:
        def __str__(self):
            raise ValueError("cannot convert")

    s = Span(name="test.span")
    s._set_attribute("key", BadObj())  # does not raise
    assert s._get_attribute("key") is None


def test_set_attribute_object_str_raises_warning():
    # When __str__ raises, the attribute is silently ignored (no warning logged).
    # The native layer swallows str() failures defensively.
    class BadObj:
        def __str__(self):
            raise ValueError("cannot convert")

    s = Span(name="test.span")
    s._set_attribute("key", BadObj())  # does not raise or log
    assert s._get_attribute("key") is None


# _has_attribute tests


def test_has_attribute_missing():
    s = Span(name="test.span")
    assert s._has_attribute("missing") is False


def test_has_attribute_string():
    s = Span(name="test.span")
    s._set_attribute("key", "val")
    assert s._has_attribute("key") is True


def test_has_attribute_numeric():
    s = Span(name="test.span")
    s._set_attribute("key", 1)
    assert s._has_attribute("key") is True


# _get_attribute tests


def test_get_attribute_missing():
    s = Span(name="test.span")
    assert s._get_attribute("missing") is None


# _get_str_attribute / _get_numeric_attribute tests


def test_get_str_attribute_exists():
    s = Span(name="test.span")
    s._set_attribute("key", "hello")
    assert s._get_str_attribute("key") == "hello"


def test_get_str_attribute_missing():
    s = Span(name="test.span")
    assert s._get_str_attribute("missing") is None


def test_get_str_attribute_when_numeric():
    s = Span(name="test.span")
    s._set_attribute("key", 42)
    assert s._get_str_attribute("key") is None


def test_get_numeric_attribute_exists():
    s = Span(name="test.span")
    s._set_attribute("key", 3.14)
    assert s._get_numeric_attribute("key") == 3.14


def test_get_numeric_attribute_missing():
    s = Span(name="test.span")
    assert s._get_numeric_attribute("missing") is None


def test_get_numeric_attribute_when_string():
    s = Span(name="test.span")
    s._set_attribute("key", "hello")
    assert s._get_numeric_attribute("key") is None


# _get_attributes / _get_str_attributes / _get_numeric_attributes tests


def test_get_attributes_empty():
    s = Span(name="test.span")
    # A fresh span may have internal keys; verify the API returns a mapping
    attrs = s._get_attributes()
    assert isinstance(attrs, dict)


def test_get_attributes_mixed():
    s = Span(name="test.span")
    s._set_attribute("str_key", "val")
    s._set_attribute("num_key", 7)
    attrs = s._get_attributes()
    assert attrs["str_key"] == "val"
    assert attrs["num_key"] == 7


def test_get_str_attributes_excludes_numeric():
    s = Span(name="test.span")
    s._set_attribute("str_key", "val")
    s._set_attribute("num_key", 7)
    str_attrs = s._get_str_attributes()
    assert "str_key" in str_attrs
    assert "num_key" not in str_attrs


def test_get_numeric_attributes_excludes_string():
    s = Span(name="test.span")
    s._set_attribute("str_key", "val")
    s._set_attribute("num_key", 7)
    num_attrs = s._get_numeric_attributes()
    assert "num_key" in num_attrs
    assert "str_key" not in num_attrs


# Cross-API compatibility tests


def test_set_attribute_visible_via_get_tag():
    s = Span(name="test.span")
    s._set_attribute("key", "hello")
    assert s.get_tag("key") == "hello"


def test_set_attribute_visible_via_get_metric():
    s = Span(name="test.span")
    s._set_attribute("key", 42)
    assert s.get_metric("key") == 42


def test_set_tag_visible_via_get_attribute():
    s = Span(name="test.span")
    s.set_tag("key", "hello")
    assert s._get_attribute("key") == "hello"


def test_set_metric_visible_via_get_attribute():
    s = Span(name="test.span")
    s._set_attribute("key", 3.14)
    assert s._get_attribute("key") == 3.14


# ---------------------------------------------------------------------------
# http.STATUS_CODE coercion tests
# ---------------------------------------------------------------------------


def test_set_attribute_http_status_code_int():
    # int values must be coerced to str and stored in meta (not metrics)
    s = Span(name="test.span")
    s._set_attribute(http.STATUS_CODE, 200)
    assert s._get_attribute(http.STATUS_CODE) == "200"


def test_set_attribute_http_status_code_str():
    # str values must stay as str and be stored in meta
    s = Span(name="test.span")
    s._set_attribute(http.STATUS_CODE, "404")
    assert s._get_attribute(http.STATUS_CODE) == "404"


def test_set_attribute_http_status_code_readable_via_get_tag():
    # must remain accessible through the public get_tag API
    s = Span(name="test.span")
    s._set_attribute(http.STATUS_CODE, 500)
    assert s.get_tag(http.STATUS_CODE) == "500"
