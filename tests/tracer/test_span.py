# -*- coding: utf-8 -*-
from functools import partial
import re
import sys
import time
import traceback
from unittest.case import SkipTest

import mock
import pytest

from ddtrace._trace._span_link import SpanLink
from ddtrace._trace._span_pointer import _SpanPointerDirection
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import ENV_KEY
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE
from ddtrace.constants import SERVICE_VERSION_KEY
from ddtrace.constants import VERSION_KEY
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.compat import PYTHON_VERSION_INFO
from ddtrace.trace import Span
from tests.subprocesstest import run_in_subprocess
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured
from tests.utils import assert_is_not_measured
from tests.utils import override_global_config


class SpanTestCase(TracerTestCase):
    def test_ids(self):
        s = Span(name="span.test")
        assert s.trace_id
        assert s.span_id
        assert not s.parent_id

        s2 = Span(name="t", trace_id=1, span_id=2, parent_id=1)
        assert s2.trace_id == 1
        assert s2.span_id == 2
        assert s2.parent_id == 1

    @run_in_subprocess(env_overrides=dict(DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED="true"))
    def test_128bit_trace_ids(self):
        s = Span(name="test.span")
        assert s.trace_id >= 2**64
        assert s._trace_id_64bits < 2**64

        trace_id_binary = format(s.trace_id, "b")
        trace_id64_binary = format(s._trace_id_64bits, "b")
        assert int(trace_id64_binary, 2) == int(trace_id_binary[-64:], 2)

    def test_tags(self):
        s = Span(name="test.span")
        s.set_tag("a", "a")
        s.set_tag("b", 1)
        s.set_tag("c", "1")

        assert s.get_tags() == dict(a="a", c="1")
        assert s.get_metrics() == dict(b=1)

    def test_numeric_tags(self):
        s = Span(name="test.span")
        s.set_tag("negative", -1)
        s.set_tag("zero", 0)
        s.set_tag("positive", 1)
        s.set_tag("large_int", 2**53)
        s.set_tag("really_large_int", (2**53) + 1)
        s.set_tag("large_negative_int", -(2**53))
        s.set_tag("really_large_negative_int", -((2**53) + 1))
        s.set_tag("float", 12.3456789)
        s.set_tag("negative_float", -12.3456789)
        s.set_tag("large_float", 2.0**53)
        s.set_tag("really_large_float", (2.0**53) + 1)

        assert s.get_tags() == dict(
            really_large_int=str(((2**53) + 1)),
            really_large_negative_int=str(-((2**53) + 1)),
        )
        assert s.get_metrics() == {
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

    def test_set_tag_bool(self):
        s = Span(name="test.span")
        s.set_tag("true", True)
        s.set_tag("false", False)

        assert s.get_tags() == dict(true="True", false="False")
        assert len(s.get_metrics()) == 0

    def test_set_baggage_item(self):
        s = Span(name="test.span")
        s.context.set_baggage_item("custom.key", "123")
        assert s.context.get_baggage_item("custom.key") == "123"

    def test_baggage_get(self):
        span1 = Span(name="test.span1")
        span1.context.set_baggage_item("item1", "123")

        span2 = Span(name="test.span2", context=span1.context)
        span2.context.set_baggage_item("item2", "456")

        assert span2.context.get_baggage_item("item1") == "123"
        assert span2.context.get_baggage_item("item2") == "456"
        assert span1.context.get_baggage_item("item1") == "123"

    def test_baggage_remove(self):
        span1 = Span(name="test.span1")
        span1.context.set_baggage_item("item1", "123")
        span1.context.set_baggage_item("item2", "456")

        span1.context.remove_baggage_item("item1")
        assert span1.context.get_baggage_item("item1") is None
        assert span1.context.get_baggage_item("item2") == "456"
        span1.context.remove_baggage_item("item2")
        assert span1.context.get_baggage_item("item2") is None

    def test_baggage_remove_all(self):
        span1 = Span(name="test.span1")
        span1.context.set_baggage_item("item1", "123")
        span1.context.set_baggage_item("item2", "456")

        span1.context.remove_all_baggage_items()
        assert span1.context.get_baggage_item("item1") is None
        assert span1.context.get_baggage_item("item2") is None

    def test_baggage_get_all(self):
        span1 = Span(name="test.span1")
        span1.context.set_baggage_item("item1", "123")
        span1.context.set_baggage_item("item2", "456")

        assert span1.context.get_all_baggage_items() == {"item1": "123", "item2": "456"}

    def test_set_tag_metric(self):
        s = Span(name="test.span")

        s.set_tag("test", "value")
        assert s.get_tags() == dict(test="value")
        assert s.get_metrics() == dict()

        s.set_tag("test", 1)
        assert s.get_tags() == dict()
        assert s.get_metrics() == dict(test=1)

    def test_set_valid_metrics(self):
        s = Span(name="test.span")
        s.set_metric("a", 0)
        s.set_metric("b", -12)
        s.set_metric("c", 12.134)
        s.set_metric("d", 1231543543265475686787869123)
        s.set_metric("e", "12.34")
        expected = {
            "a": 0,
            "b": -12,
            "c": 12.134,
            "d": 1231543543265475686787869123,
            "e": 12.34,
        }
        assert s.get_metrics() == expected

    def test_set_invalid_metric(self):
        s = Span(name="test.span")

        invalid_metrics = [None, {}, [], s, "quarante-douze", float("nan"), float("inf"), 1j]

        for i, m in enumerate(invalid_metrics):
            k = str(i)
            s.set_metric(k, m)
            assert s.get_metric(k) is None

    def test_set_numpy_metric(self):
        try:
            import numpy as np
        except ImportError:
            raise SkipTest("numpy not installed")
        s = Span(name="test.span")
        s.set_metric("a", np.int64(1))
        assert s.get_metric("a") == 1
        assert type(s.get_metric("a")) == float

    def test_tags_not_string(self):
        # ensure we can cast as strings
        class Foo(object):
            def __repr__(self):
                1 / 0

        s = Span(name="test.span")
        s.set_tag("a", Foo())

    def test_finish(self):
        # ensure span.finish() marks the end time of the span
        s = Span("test.span")
        sleep = 0.05
        time.sleep(sleep)
        s.finish()
        assert s.duration >= sleep, "%s < %s" % (s.duration, sleep)

    def test_finish_no_tracer(self):
        # ensure finish works with no tracer without raising exceptions
        s = Span(name="test.span")
        s.finish()

    def test_finish_called_multiple_times(self):
        # we should only record a span the first time finish is called on it
        s = Span("bar")
        s.finish()
        s.finish()

    def test_finish_set_span_duration(self):
        # If set the duration on a span, the span should be recorded with this
        # duration
        s = Span(name="test.span")
        s.duration = 1337.0
        s.finish()
        assert s.duration == 1337.0

    def test_setter_casts_duration_ns_as_int(self):
        s = Span(name="test.span")
        s.duration = 3.2
        s.finish()
        assert s.duration == 3.2
        assert s.duration_ns == 3200000000
        assert isinstance(s.duration_ns, int)

    def test_get_span_returns_none_by_default(self):
        s = Span(name="test.span")
        assert s.duration is None

    def test_traceback_with_error(self):
        s = Span("test.span")
        try:
            1 / 0
        except ZeroDivisionError:
            s.set_traceback()
        else:
            assert 0, "should have failed"

        assert s.error
        assert "by zero" in s.get_tag(ERROR_MSG)
        assert "ZeroDivisionError" in s.get_tag(ERROR_TYPE)

    def test_traceback_without_error(self):
        s = Span("test.span")
        s.set_traceback()
        assert not s.error
        assert not s.get_tag(ERROR_MSG)
        assert not s.get_tag(ERROR_TYPE)
        assert "in test_traceback_without_error" in s.get_tag(ERROR_STACK)

    def test_custom_traceback_size(self):
        tb_length_limit = 11
        with override_global_config(dict(_span_traceback_max_size=tb_length_limit)):
            s = Span("test.span")
            s.set_traceback()
            stack = s.get_tag(ERROR_STACK)
            assert len(stack.splitlines()) == tb_length_limit * 2, "stacktrace should contain two lines per entry"

    def test_custom_traceback_size_with_error(self):
        tb_length_limit = 2
        with override_global_config(dict(_span_traceback_max_size=tb_length_limit)):
            s = Span("test.span")

            def divide_by_zero():
                1 / 0

            # Wrapper function to generate a larger traceback
            def wrapper():
                divide_by_zero()

            try:
                wrapper()
            except ZeroDivisionError:
                s.set_traceback()
            else:
                assert 0, "should have failed"

            stack = s.get_tag(ERROR_STACK)
            assert stack, "No error stack collected"
            # one header "Traceback (most recent call last):" and one footer "ZeroDivisionError: division by zero"
            header_and_footer_lines = 2
            multiplier = 2
            if PYTHON_VERSION_INFO >= (3, 13):
                # Python 3.13 adds extra lines to the traceback:
                #   File dd-trace-py/tests/tracer/test_span.py", line 279, in test_custom_traceback_size_with_error
                #     wrapper()
                #     ~~~~~~~^^
                multiplier = 3
            elif PYTHON_VERSION_INFO >= (3, 11):
                # Python 3.11 adds one extra line to the traceback:
                #   File dd-trace-py/tests/tracer/test_span.py", line 272, in divide_by_zero
                #      1 / 0
                #      ~~^~~
                #      ZeroDivisionError: division by zero
                header_and_footer_lines += 1

            assert (
                len(stack.splitlines()) == tb_length_limit * multiplier + header_and_footer_lines
            ), "stacktrace should contain two lines per entry"

    def test_ctx_mgr(self):
        s = Span("bar")
        assert not s.duration
        assert not s.error

        e = Exception("boo")
        try:
            with s:
                time.sleep(0.01)
                raise e
        except Exception as out:
            assert out == e
            assert s.duration > 0, s.duration
            assert s.error
            assert s.get_tag(ERROR_MSG) == "boo"
            assert "Exception" in s.get_tag(ERROR_TYPE)
            assert s.get_tag(ERROR_STACK)

        else:
            assert 0, "should have failed"

    def test_span_type(self):
        s = Span(name="test.span", service="s", resource="r", span_type=SpanTypes.WEB)
        s.finish()

        assert s.span_type == "web"

    @mock.patch("ddtrace._trace.span.log")
    def test_numeric_tags_none(self, span_log):
        s = Span(name="test.span")
        s.set_tag("noneval", None)
        assert len(s.get_metrics()) == 0

    def test_numeric_tags_value(self):
        s = Span(name="test.span")
        s.set_tag("point5", 0.5)
        expected = {"point5": 0.5}
        assert s.get_metrics() == expected

    def test_numeric_tags_bad_value(self):
        s = Span(name="test.span")
        s.set_tag("somestring", "Hello")
        assert len(s.get_metrics()) == 0

    def test_set_tag_none(self):
        s = Span(name="root.span", service="s", resource="r")
        assert s.get_tags() == dict()

        s.set_tag("custom.key", "100")

        assert s.get_tags() == {"custom.key": "100"}

        s.set_tag("custom.key", None)

        assert s.get_tags() == {"custom.key": "None"}

    def test_duration_zero(self):
        s = Span(name="foo.bar", service="s", resource="r", start=123)
        s.finish(finish_time=123)
        assert s.duration_ns == 0
        assert s.duration == 0

    def test_start_int(self):
        s = Span(name="foo.bar", service="s", resource="r", start=123)
        assert s.start == 123
        assert s.start_ns == 123000000000

        s = Span(name="foo.bar", service="s", resource="r", start=123.123)
        assert s.start == 123.123
        assert s.start_ns == 123123000000

        s = Span(name="foo.bar", service="s", resource="r", start=123.123)
        s.start = 234567890.0
        assert s.start == 234567890
        assert s.start_ns == 234567890000000000

    def test_duration_int(self):
        s = Span(name="foo.bar", service="s", resource="r")
        s.finish()
        assert isinstance(s.duration_ns, int)
        assert isinstance(s.duration, float)

        s = Span(name="foo.bar", service="s", resource="r", start=123)
        s.finish(finish_time=123.2)
        assert s.duration_ns == 200000000
        assert s.duration == 0.2

        s = Span(name="foo.bar", service="s", resource="r", start=123.1)
        s.finish(finish_time=123.2)
        assert s.duration_ns == 100000000
        assert s.duration == 0.1

        s = Span(name="foo.bar", service="s", resource="r", start=122)
        s.finish(finish_time=123)
        assert s.duration_ns == 1000000000
        assert s.duration == 1

    def test_set_tag_version(self):
        s = Span(name="test.span")
        s.set_tag(VERSION_KEY, "1.2.3")
        assert s.get_tag(VERSION_KEY) == "1.2.3"
        assert s.get_tag(SERVICE_VERSION_KEY) is None

        s.set_tag(SERVICE_VERSION_KEY, "service.version")
        assert s.get_tag(VERSION_KEY) == "service.version"
        assert s.get_tag(SERVICE_VERSION_KEY) == "service.version"

    def test_set_tag_env(self):
        s = Span(name="test.span")
        s.set_tag(ENV_KEY, "prod")
        assert s.get_tag(ENV_KEY) == "prod"

    def test_span_links(self):
        s1 = Span(name="test.span1")

        s2 = Span(name="test.span2", span_id=1, trace_id=2)
        s2.context._meta["tracestate"] = "congo=t61rcWkgMzE"
        s2.context.sampling_priority = 1

        link_attributes = {
            "link.name": "s1_to_s2",
            "link.kind": "scheduled_by",
            "key1": "value2",
            "key2": [True, 2, ["hello", 4, ["5", "6asda"]]],
        }
        s1.link_span(s2.context, link_attributes)

        assert [link for link in s1._links if link.span_id == s2.span_id] == [
            SpanLink(
                trace_id=s2.trace_id,
                span_id=s2.span_id,
                tracestate="dd=s:1,congo=t61rcWkgMzE",
                flags=1,
                attributes=link_attributes,
            )
        ]

    def test_init_with_span_links(self):
        links = [
            SpanLink(trace_id=1, span_id=10),
            SpanLink(trace_id=1, span_id=20),
            SpanLink(trace_id=2, span_id=30, flags=0),
            SpanLink(trace_id=2, span_id=30, flags=1),
        ]
        s = Span(name="test.span", links=links)

        assert s._links == [
            links[0],
            links[1],
            # duplicate links are overwritten (last one wins)
            links[3],
        ]

    def test_set_span_link(self):
        s = Span(name="test.span")
        s.set_link(trace_id=1, span_id=10)
        s.set_link(trace_id=1, span_id=20)
        s.set_link(trace_id=2, span_id=30, flags=0)

        with mock.patch("ddtrace._trace.span.log") as log:
            s.set_link(trace_id=2, span_id=30, flags=1)
        log.debug.assert_called_once_with(
            "Span %d already linked to span %d. Overwriting existing link: %s",
            s.span_id,
            30,
            mock.ANY,
        )

        assert s._links == [
            SpanLink(trace_id=1, span_id=10),
            SpanLink(trace_id=1, span_id=20),
            # duplicate links are overwritten (last one wins)
            SpanLink(trace_id=2, span_id=30, flags=1),
        ]

    # span links cannot have a span_id or trace_id value of 0 or less
    def test_span_links_error_with_id_0(self):
        with pytest.raises(ValueError) as exc_trace:
            SpanLink(span_id=1, trace_id=0)
        with pytest.raises(ValueError) as exc_span:
            SpanLink(span_id=0, trace_id=1)
        assert str(exc_span.value) == "span_id must be > 0. Value is 0"
        assert str(exc_trace.value) == "trace_id must be > 0. Value is 0"

    def test_span_pointers(self):
        s = Span(name="test.span")

        s.set_link(trace_id=1, span_id=10)
        s._add_span_pointer(
            pointer_kind="some-kind",
            pointer_direction=_SpanPointerDirection.DOWNSTREAM,
            pointer_hash="some-hash",
            extra_attributes={"extra": "attribute"},
        )
        s.set_link(trace_id=1, span_id=15)
        s._add_span_pointer(
            pointer_kind="another-kind",
            pointer_direction=_SpanPointerDirection.UPSTREAM,
            pointer_hash="another-hash",
            extra_attributes={"more-extra": "attribute"},
        )

        # We don't particularly care about how they're stored, but we do care
        # that they are serizlied correctly into the _links when they get
        # shipped.
        assert [link.to_dict() for link in s._links] == [
            {
                "trace_id": "00000000000000000000000000000001",
                "span_id": "000000000000000a",
            },
            {
                "trace_id": "00000000000000000000000000000000",
                "span_id": "0000000000000000",
                "attributes": {
                    "link.kind": "span-pointer",
                    "ptr.kind": "some-kind",
                    "ptr.dir": "d",
                    "ptr.hash": "some-hash",
                    "extra": "attribute",
                },
            },
            {
                "trace_id": "00000000000000000000000000000001",
                "span_id": "000000000000000f",
            },
            {
                "trace_id": "00000000000000000000000000000000",
                "span_id": "0000000000000000",
                "attributes": {
                    "link.kind": "span-pointer",
                    "ptr.kind": "another-kind",
                    "ptr.dir": "u",
                    "ptr.hash": "another-hash",
                    "more-extra": "attribute",
                },
            },
        ]

    def test_span_record_exception(self):
        span = self.start_span("span")
        try:
            raise RuntimeError("bim")
        except RuntimeError as e:
            span.record_exception(e)
        span.finish()

        span.assert_span_event_count(1)
        span.assert_span_event_attributes(
            0, {"exception.type": "builtins.RuntimeError", "exception.message": "bim", "exception.escaped": False}
        )

    def test_span_record_multiple_exceptions(self):
        span = self.start_span("span")
        try:
            raise RuntimeError("bim")
        except RuntimeError as e:
            span.record_exception(e)

        try:
            raise RuntimeError("bam")
        except RuntimeError as e:
            span.record_exception(e)
        span.finish()

        span.assert_span_event_count(2)
        span.assert_span_event_attributes(
            0, {"exception.type": "builtins.RuntimeError", "exception.message": "bim", "exception.escaped": False}
        )
        span.assert_span_event_attributes(
            1, {"exception.type": "builtins.RuntimeError", "exception.message": "bam", "exception.escaped": False}
        )

    def test_span_record_escaped_exception(self):
        exc = RuntimeError("bim")
        span = self.start_span("span")
        try:
            raise exc
        except RuntimeError as e:
            span.record_exception(e, escaped=True)
        span.finish()

        span.assert_matches(
            error=1,
            meta={
                "error.message": str(exc),
                "error.type": "%s.%s" % (exc.__class__.__module__, exc.__class__.__name__),
            },
        )
        span.assert_span_event_count(1)
        span.assert_span_event_attributes(
            0, {"exception.type": "builtins.RuntimeError", "exception.message": "bim", "exception.escaped": True}
        )


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
    s.set_tag(_SPAN_MEASURED_KEY, value)
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
    s.set_tag(_SPAN_MEASURED_KEY, True)
    assert_is_measured(s)

    s.set_tag(_SPAN_MEASURED_KEY, False)
    assert_is_not_measured(s)

    s.set_tag(_SPAN_MEASURED_KEY)
    assert_is_measured(s)


@mock.patch("ddtrace._trace.span.log")
def test_span_key(span_log):
    # Span tag keys must be strings
    s = Span(name="test.span")

    s.set_tag(123, True)
    span_log.warning.assert_called_once_with("Ignoring tag pair %s:%s. Key must be a string.", 123, True)
    assert s.get_tag(123) is None
    assert s.get_tag("123") is None

    span_log.reset_mock()

    s.set_tag(None, "val")
    span_log.warning.assert_called_once_with("Ignoring tag pair %s:%s. Key must be a string.", None, "val")
    assert s.get_tag(123.32) is None


def test_spans_finished():
    span = Span(None)
    assert span.finished is False
    assert span.duration_ns is None

    span.finished = True
    assert span.finished is True
    assert span.duration_ns is not None
    duration = span.duration_ns

    span.finished = True
    assert span.finished is True
    assert span.duration_ns == duration

    span.finished = False
    assert span.finished is False

    span.finished = True
    assert span.finished is True
    assert span.duration_ns != duration


def test_span_unicode_set_tag():
    span = Span(None)
    span.set_tag("key", "😌")
    span.set_tag("😐", "😌")
    span.set_tag_str("key", "😌")
    span.set_tag_str("😐", "😌")


@pytest.mark.skipif(sys.version_info.major != 2, reason="This test only applies Python 2")
@mock.patch("ddtrace._trace.span.log")
def test_span_binary_unicode_set_tag(span_log):
    span = Span(None)
    span.set_tag("key", "🤔")
    span.set_tag_str("key_str", "🤔")
    # only span.set_tag() will fail
    span_log.warning.assert_called_once_with("error setting tag %s, ignoring it", "key", exc_info=True)
    assert "key" not in span.get_tags()
    assert span.get_tag("key_str") == "🤔"


@pytest.mark.skipif(sys.version_info.major == 2, reason="This test does not apply to Python 2")
@mock.patch("ddtrace._trace.span.log")
def test_span_bytes_string_set_tag(span_log):
    span = Span(None)
    span.set_tag("key", b"\xf0\x9f\xa4\x94")
    span.set_tag_str("key_str", b"\xf0\x9f\xa4\x94")
    assert span.get_tag("key") == "b'\\xf0\\x9f\\xa4\\x94'"
    assert span.get_tag("key_str") == "🤔"
    span_log.warning.assert_not_called()


@mock.patch("ddtrace._trace.span.log")
def test_span_encoding_set_str_tag(span_log):
    span = Span(None)
    span.set_tag_str("foo", "/?foo=bar&baz=정상처리".encode("euc-kr"))
    span_log.warning.assert_not_called()
    assert span.get_tag("foo") == "/?foo=bar&baz=����ó��"


def test_span_nonstring_set_str_tag_exc():
    span = Span(None)
    with pytest.raises(TypeError):
        span.set_tag_str("foo", dict(a=1))
    assert "foo" not in span.get_tags()


@mock.patch("ddtrace._trace.span.log")
def test_span_nonstring_set_str_tag_warning(span_log):
    with override_global_config(dict(_raise=False)):
        span = Span(None)
        span.set_tag_str("foo", dict(a=1))
        span_log.warning.assert_called_once_with(
            "Failed to set text tag '%s'",
            "foo",
            exc_info=True,
        )


def test_span_ignored_exceptions():
    s = Span(None)
    s._ignore_exception(ValueError)

    with pytest.raises(ValueError):
        with s:
            raise ValueError()

    assert s.error == 0
    assert s.get_tag(ERROR_MSG) is None
    assert s.get_tag(ERROR_TYPE) is None
    assert s.get_tag(ERROR_STACK) is None

    s = Span(None)
    s._ignore_exception(ValueError)

    with pytest.raises(ValueError):
        with s:
            raise ValueError()

    with pytest.raises(RuntimeError):
        with s:
            raise RuntimeError()

    assert s.error == 1
    assert s.get_tag(ERROR_MSG) is not None
    assert "RuntimeError" in s.get_tag(ERROR_TYPE)
    assert s.get_tag(ERROR_STACK) is not None


def test_span_ignored_exception_multi():
    s = Span(None)
    s._ignore_exception(ValueError)
    s._ignore_exception(RuntimeError)

    with pytest.raises(ValueError):
        with s:
            raise ValueError()

    with pytest.raises(RuntimeError):
        with s:
            raise RuntimeError()

    assert s.error == 0
    assert s.get_tag(ERROR_MSG) is None
    assert s.get_tag(ERROR_TYPE) is None
    assert s.get_tag(ERROR_STACK) is None


def test_span_ignored_exception_subclass():
    s = Span(None)
    s._ignore_exception(Exception)

    with pytest.raises(ValueError):
        with s:
            raise ValueError()

    with pytest.raises(RuntimeError):
        with s:
            raise RuntimeError()

    assert s.error == 0
    assert s.get_tag(ERROR_MSG) is None
    assert s.get_tag(ERROR_TYPE) is None
    assert s.get_tag(ERROR_STACK) is None


def test_on_finish_single_callback():
    m = mock.Mock()
    s = Span("test", on_finish=[m])
    m.assert_not_called()
    s.finish()
    m.assert_called_once_with(s)


def test_on_finish_multi_callback():
    m1 = mock.Mock()
    m2 = mock.Mock()
    s = Span("test", on_finish=[m1, m2])
    s.finish()
    m1.assert_called_once_with(s)
    m2.assert_called_once_with(s)


@pytest.mark.parametrize("arg", ["span_id", "trace_id", "parent_id"])
def test_span_preconditions(arg):
    Span("test", **{arg: None})
    with pytest.raises(TypeError):
        Span("test", **{arg: "foo"})


def test_span_pprint():
    root = Span("test.span", service="s", resource="r", span_type=SpanTypes.WEB)
    root.set_tag("t", "v")
    root.set_metric("m", 1.0)
    root._add_event("message", {"importance": 10}, 16789898242)
    root.set_link(trace_id=99, span_id=10, attributes={"link.name": "s1_to_s2", "link.kind": "scheduled_by"})

    root.finish()
    actual = root._pprint()
    assert "name='test.span'" in actual
    assert "service='s'" in actual
    assert "resource='r'" in actual
    assert "type='web'" in actual
    assert "error=0" in actual
    assert "tags={'t': 'v'}" in actual
    assert "metrics={'m': 1.0}" in actual
    assert "events='name=message time=16789898242 attributes=importance:10'" in actual
    assert (
        "links='trace_id=99 span_id=10 attributes=link.name:s1_to_s2,link.kind:scheduled_by "
        "tracestate=None flags=None dropped_attributes=0'" in actual
    )
    assert re.search("id=[0-9]+", actual) is not None
    assert re.search("trace_id=[0-9]+", actual) is not None
    assert "parent_id=None" in actual
    assert re.search("duration=[0-9.]+", actual) is not None
    assert re.search("start=[0-9.]+", actual) is not None
    assert re.search("end=[0-9.]+", actual) is not None

    root = Span("test.span", service="s", resource="r", span_type=SpanTypes.WEB)
    actual = root._pprint()
    assert "duration=None" in actual
    assert "end=None" in actual

    root = Span("test.span", service="s", resource="r", span_type=SpanTypes.WEB)
    root.error = 1
    actual = root._pprint()
    assert "error=1" in actual

    root = Span("test.span", service="s", resource="r", span_type=SpanTypes.WEB)
    root.set_tag("😌", "😌")
    actual = root._pprint()
    assert "tags={'😌': '😌'}" in actual

    root = Span("test.span", service=object())
    actual = root._pprint()
    assert "service=<object object at" in actual


def test_manual_context_usage():
    span1 = Span("span1")
    span2 = Span("span2", context=span1.context)

    span2.context.sampling_priority = 2
    assert span1.context.sampling_priority == 2

    span1.context.sampling_priority = 1
    assert span2.context.sampling_priority == 1
    assert span1.context.sampling_priority == 1


def test_set_exc_info_with_str_override():
    span = Span("span")

    class CustomException(Exception):
        def __str__(self):
            raise Exception("A custom exception")

    try:
        raise CustomException()
    except Exception:
        type_, value_, traceback_ = sys.exc_info()
        span.set_exc_info(type_, value_, traceback_)

    span.finish()
    assert span.get_tag(ERROR_MSG) == "CustomException"
    assert span.get_tag(ERROR_STACK) is not None
    assert span.get_tag(ERROR_TYPE) == "tests.tracer.test_span.CustomException"


def test_set_exc_info_with_systemexit():
    def get_exception_span():
        span = Span("span1")
        try:
            sys.exit(0)
        except SystemExit:
            type_, value_, traceback_ = sys.exc_info()
            span.set_exc_info(type_, value_, traceback_)
        return span

    exception_span = get_exception_span()
    assert not exception_span.error


def test_set_exc_info_with_unicode():
    def get_exception_span(exception):
        span = Span("span1")
        try:
            raise exception
        except Exception:
            type_, value_, traceback_ = sys.exc_info()
            span.set_exc_info(type_, value_, traceback_)
        return span

    exception_span = get_exception_span(Exception("DataDog/水"))
    assert "DataDog/水" == exception_span.get_tag(ERROR_MSG)


def test_span_exception_core_event():
    s = Span(None)
    e = ValueError()

    event_handler_called = False

    @partial(core.on, "span.exception")
    def _(span, *exc_info):
        nonlocal event_handler_called

        assert span is s
        assert exc_info[1] is e

        event_handler_called = True

    try:
        with s:
            raise e
    except ValueError:
        assert event_handler_called
    else:
        raise AssertionError("should have raised")
    finally:
        core.reset_listeners("span.exception")


def test_get_traceback_exceeds_max_value_length():
    """Test with a long traceback that should be truncated."""

    def deep_error(n):
        if n > 0:
            deep_error(n - 1)
        else:
            raise RuntimeError("Deep recursion error")

    exc_type, exc_val, exc_tb = None, None, None
    try:
        deep_error(100)  # Create a large traceback
    except Exception as e:
        exc_type, exc_val, exc_tb = e.__class__, e, e.__traceback__

    span = Span("test.span")
    with mock.patch("ddtrace._trace.span.MAX_SPAN_META_VALUE_LEN", 260):
        result = span._get_traceback(exc_type, exc_val, exc_tb, limit=100)
    assert "Deep recursion error" in result
    assert len(result) <= 260  # Should be truncated


def test_get_traceback_exact_limit():
    """Test a case where the traceback length is exactly at the limit."""

    def deep_error(n):
        if n > 0:
            deep_error(n - 1)
        else:
            raise RuntimeError("Deep recursion error")

    exc_type, exc_val, exc_tb = None, None, None
    try:
        deep_error(100)  # Create a large traceback
    except Exception as e:
        exc_type, exc_val, exc_tb = e.__class__, e, e.__traceback__

    span = Span("test.span")
    formatted_exception = traceback.format_exception(exc_type, exc_val, exc_tb)
    formatted_exception = [s + "\n" for item in formatted_exception for s in item.split("\n") if s]
    exc_len = len(formatted_exception)
    result = span._get_traceback(exc_type, exc_val, exc_tb, limit=exc_len)
    split_result = result.splitlines()
    split_result = [s + "\n" for item in split_result for s in item.split("\n") if s]

    if PYTHON_VERSION_INFO >= (3, 11):
        exc_len -= 1  # From Python 3.11, adds an extra line to the traceback

    assert len(split_result) == exc_len - 2  # Should be exactly the same length as the traceback


def test_get_traceback_honors_config_traceback_max_size():
    class CustomConfig:
        _span_traceback_max_size = 2  # Force a zero limit

    def deep_error(n):
        if n > 0:
            deep_error(n - 1)
        else:
            raise RuntimeError("Deep recursion error")

    exc_type, exc_val, exc_tb = None, None, None
    try:
        deep_error(100)  # Create a large traceback
    except Exception as e:
        exc_type, exc_val, exc_tb = e.__class__, e, e.__traceback__

    span = Span("test.span")
    with mock.patch("ddtrace._trace.span.config", CustomConfig):
        result = span._get_traceback(exc_type, exc_val, exc_tb)

    assert isinstance(result, str)
    split_result = result.splitlines()
    split_result = [s + "\n" for item in split_result for s in item.split("\n") if s]
    assert len(split_result) < 8  # Value is 5 for Python 3.10
    assert len(result) < 410  # Value is 377 for Python 3.10
