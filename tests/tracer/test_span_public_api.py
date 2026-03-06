# -*- coding: utf-8 -*-
"""
Tests for the public Span tag/metric API: set_tag, set_metric, get_tag, get_metric,
set_tags, get_tags, set_metrics, get_metrics.

This file intentionally uses the public API and is excluded from the
span-set-tag / span-get-tag / span-set-metric / span-get-metric ast-grep rules.
"""
import sys
from unittest.case import SkipTest

import mock
import pytest

from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import ENV_KEY
from ddtrace.constants import SERVICE_VERSION_KEY
from ddtrace.constants import VERSION_KEY
from ddtrace.trace import Span
from tests.utils import assert_is_measured
from tests.utils import assert_is_not_measured
from tests.utils import override_global_config


def test_tags():
    s = Span(name="test.span")
    s._set_attribute("a", "a")
    s._set_attribute("b", 1)
    s._set_attribute("c", "1")

    assert s._get_str_attributes() == dict(a="a", c="1")
    assert s._get_numeric_attributes() == dict(b=1)


def test_numeric_tags():
    s = Span(name="test.span")
    s._set_attribute("negative", -1)
    s._set_attribute("zero", 0)
    s._set_attribute("positive", 1)
    s._set_attribute("large_int", 2**53)
    s._set_attribute("really_large_int", (2**53) + 1)
    s._set_attribute("large_negative_int", -(2**53))
    s._set_attribute("really_large_negative_int", -((2**53) + 1))
    s._set_attribute("float", 12.3456789)
    s._set_attribute("negative_float", -12.3456789)
    s._set_attribute("large_float", 2.0**53)
    s._set_attribute("really_large_float", (2.0**53) + 1)

    assert s._get_str_attributes() == dict(
        really_large_int=str(((2**53) + 1)),
        really_large_negative_int=str(-((2**53) + 1)),
    )
    assert s._get_numeric_attributes() == {
        "negative": -1,
        "zero": 0,
        "positive": 1,
        "large_int": 2**53,
        "large_negative_int": -(2**53),
        "float": 12.3456789,
        "negative_float": -12.3456789,
        "large_float": 2.0**53,
        "really_large_float": (2.0**53) + 1,
    }


def test_set_tag_bool():
    s = Span(name="test.span")
    s._set_attribute("true", True)
    s._set_attribute("false", False)

    assert s._get_str_attributes() == dict(true="True", false="False")
    assert len(s._get_numeric_attributes()) == 0


def test_set_tag_metric():
    s = Span(name="test.span")

    s._set_attribute("test", "value")
    assert s._get_str_attributes() == dict(test="value")
    assert s._get_numeric_attributes() == dict()

    s._set_attribute("test", 1)
    assert s._get_str_attributes() == dict()
    assert s._get_numeric_attributes() == dict(test=1)


def test_set_valid_metrics():
    s = Span(name="test.span")
    s._set_attribute("a", 0)
    s._set_attribute("b", -12)
    s._set_attribute("c", 12.134)
    s._set_attribute("d", 1231543543265475686787869123)
    s._set_attribute("e", "12.34")
    expected = {
        "a": 0,
        "b": -12,
        "c": 12.134,
        "d": 1231543543265475686787869123,
        "e": 12.34,
    }
    assert s._get_numeric_attributes() == expected


def test_set_invalid_metric():
    s = Span(name="test.span")

    invalid_metrics = [None, {}, [], s, "quarante-douze", float("nan"), float("inf"), 1j]

    for i, m in enumerate(invalid_metrics):
        k = str(i)
        s._set_attribute(k, m)
        assert s._get_numeric_attribute(k) is None


def test_set_numpy_metric():
    try:
        import numpy as np
    except ImportError:
        raise SkipTest("numpy not installed")
    s = Span(name="test.span")
    s._set_attribute("a", np.int64(1))
    assert s._get_numeric_attribute("a") == 1
    assert type(s._get_numeric_attribute("a")) == float


def test_tags_not_string():
    # ensure we can cast as strings
    class Foo(object):
        def __repr__(self):
            1 / 0

    s = Span(name="test.span")
    s._set_attribute("a", Foo())


@mock.patch("ddtrace._trace.span.log")
def test_numeric_tags_none(span_log):
    s = Span(name="test.span")
    s._set_attribute("noneval", None)
    assert len(s._get_numeric_attributes()) == 0


def test_numeric_tags_value():
    s = Span(name="test.span")
    s._set_attribute("point5", 0.5)
    expected = {"point5": 0.5}
    assert s._get_numeric_attributes() == expected


def test_numeric_tags_bad_value():
    s = Span(name="test.span")
    s._set_attribute("somestring", "Hello")
    assert len(s._get_numeric_attributes()) == 0


def test_set_tag_none():
    s = Span(name="root.span", service="s", resource="r")
    assert s._get_str_attributes() == dict()

    s._set_attribute("custom.key", "100")

    assert s._get_str_attributes() == {"custom.key": "100"}

    s._set_attribute("custom.key", None)

    assert s._get_str_attributes() == {"custom.key": "None"}


def test_set_tag_version():
    s = Span(name="test.span")
    s._set_attribute(VERSION_KEY, "1.2.3")
    assert s._get_str_attribute(VERSION_KEY) == "1.2.3"
    assert s._get_str_attribute(SERVICE_VERSION_KEY) is None

    s._set_attribute(SERVICE_VERSION_KEY, "service.version")
    assert s._get_str_attribute(VERSION_KEY) == "service.version"
    assert s._get_str_attribute(SERVICE_VERSION_KEY) == "service.version"


def test_set_tag_env():
    s = Span(name="test.span")
    s._set_attribute(ENV_KEY, "prod")
    assert s._get_str_attribute(ENV_KEY) == "prod"


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
    s._set_attribute(_SPAN_MEASURED_KEY, value)
    assertion(s)


def test_set_tag_measured_not_set():
    # Span is not measured by default
    s = Span(name="test.span")
    assert_is_not_measured(s)


def test_set_tag_measured_no_value():
    s = Span(name="test.span")
    s.set_tag(_SPAN_MEASURED_KEY)
    assert_is_measured(s)


def test_set_tag_measured_change_value():
    s = Span(name="test.span")
    s._set_attribute(_SPAN_MEASURED_KEY, True)
    assert_is_measured(s)

    s._set_attribute(_SPAN_MEASURED_KEY, False)
    assert_is_not_measured(s)

    s.set_tag(_SPAN_MEASURED_KEY)
    assert_is_measured(s)


def test_span_unicode_set_tag():
    span = Span(None)
    span._set_attribute("key", "😌")
    span._set_attribute("😐", "😌")
    span._set_attribute("key", "😌")
    span._set_attribute("😐", "😌")


@pytest.mark.skipif(sys.version_info.major != 2, reason="This test only applies Python 2")
@mock.patch("ddtrace._trace.span.log")
def test_span_binary_unicode_set_tag(span_log):
    span = Span(None)
    span._set_attribute("key", "🤔")
    span._set_attribute("key_str", "🤔")
    # only span.set_tag() will fail
    span_log.warning.assert_called_once_with("error setting tag %s, ignoring it", "key", exc_info=True)
    assert "key" not in span._get_str_attributes()
    assert span._get_str_attribute("key_str") == "🤔"


@pytest.mark.skipif(sys.version_info.major == 2, reason="This test does not apply to Python 2")
@mock.patch("ddtrace._trace.span.log")
def test_span_bytes_string_set_tag(span_log):
    span = Span(None)
    span._set_attribute("key", b"\xf0\x9f\xa4\x94")
    span._set_attribute("key_str", b"\xf0\x9f\xa4\x94")
    assert span._get_str_attribute("key") == "b'\\xf0\\x9f\\xa4\\x94'"
    assert span._get_str_attribute("key_str") == "🤔"
    span_log.warning.assert_not_called()
