# -*- coding: utf-8 -*-
from functools import partial
import sys
import time
import traceback

import mock
import pytest

from ddtrace._trace._span_link import SpanLink
from ddtrace._trace._span_pointer import _SpanPointerDirection
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.compat import PYTHON_VERSION_INFO
from ddtrace.trace import Span
from tests.subprocesstest import run_in_subprocess
from tests.utils import TracerTestCase
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

    @run_in_subprocess(env_overrides=dict(DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED="true"))
    def test_128bit_trace_id_config_deprecation_warning(self):
        """Test that setting DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED emits a deprecation warning."""
        import warnings

        from ddtrace.internal.settings._config import Config
        from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning

        warnings.simplefilter("always")
        with warnings.catch_warnings(record=True) as warns:
            Config()

        deprecation_warns = [w for w in warns if issubclass(w.category, DDTraceDeprecationWarning)]
        assert len(deprecation_warns) >= 1
        assert "DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED is deprecated" in str(deprecation_warns[0].message)

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

            assert len(stack.splitlines()) == tb_length_limit * multiplier + header_and_footer_lines, (
                "stacktrace should contain two lines per entry"
            )

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

        # Attributes containing nested lists are flattened when stored natively;
        # compare via to_dict() which is the canonical serialized form.
        expected = SpanLink(
            trace_id=s2.trace_id,
            span_id=s2.span_id,
            tracestate="dd=s:1,congo=t61rcWkgMzE",
            flags=1,
            attributes=link_attributes,
        )
        actual = [link for link in s1._get_links() if link.span_id == s2.span_id]
        assert len(actual) == 1
        assert actual[0].to_dict() == expected.to_dict()

    def test_init_with_span_links(self):
        links = [
            SpanLink(trace_id=1, span_id=10),
            SpanLink(trace_id=1, span_id=20),
            SpanLink(trace_id=2, span_id=30, flags=0),
            SpanLink(trace_id=2, span_id=30, flags=1),
        ]
        s = Span(name="test.span", links=links)

        assert s._get_links() == [
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

        s.set_link(trace_id=2, span_id=30, flags=1)

        assert s._get_links() == [
            SpanLink(trace_id=1, span_id=10),
            SpanLink(trace_id=1, span_id=20),
            # duplicate links are overwritten (last one wins)
            SpanLink(trace_id=2, span_id=30, flags=1),
        ]

    def test_span_link_flags_none_vs_zero_roundtrip(self):
        # flags=None and flags=0 are distinct values. Both must round-trip correctly
        # through native storage. Internally, bit 31 of the native u32 flags field
        # acts as a "flags present" sentinel: None->0, Some(f)->f|0x8000_0000.
        s = Span(name="test.span")
        s.set_link(trace_id=1, span_id=10)  # flags=None
        s.set_link(trace_id=2, span_id=20, flags=0)  # flags=0 (explicitly set)
        s.set_link(trace_id=3, span_id=30, flags=1)  # flags=1

        links = s._get_links()
        assert links[0].flags is None
        assert links[1].flags == 0
        assert links[2].flags == 1

    def test_span_link_flags_out_of_range_behavior(self):
        # flags is stored as u32 with bit 31 as "present" sentinel (None->0, Some(f)->f|0x8000_0000).
        # Values outside the 31-bit range are silently truncated — this documents the behavior.
        # W3C tracecontext flags are 8 bits, so this only affects pathological inputs.
        s = Span(name="test.span")
        # 0x1_0000_0000 as u32 truncates to 0; ORed with sentinel -> 0x8000_0000; strip sentinel -> 0
        s.set_link(trace_id=1, span_id=10, flags=0x1_0000_0000)
        # -1 as i64 -> 0xFFFF_FFFF as u32; ORed with sentinel -> 0xFFFF_FFFF; strip bit 31 -> 0x7FFF_FFFF
        s.set_link(trace_id=1, span_id=20, flags=-1)

        links = s._get_links()
        assert links[0].flags == 0  # 0x1_0000_0000 truncated to 0; sentinel set then stripped -> 0
        assert links[1].flags == 0x7FFF_FFFF  # -1 -> 0xFFFF_FFFF; sentinel strip -> 0x7FFF_FFFF

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
        # that they are serialized correctly into the _links when they get
        # shipped.
        assert [link.to_dict() for link in s._get_links()] == [
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
        span.assert_span_event_attributes(0, {"exception.type": "builtins.RuntimeError", "exception.message": "bim"})

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
        span.assert_span_event_attributes(0, {"exception.type": "builtins.RuntimeError", "exception.message": "bim"})
        span.assert_span_event_attributes(1, {"exception.type": "builtins.RuntimeError", "exception.message": "bam"})

    def test_span_record_exception_with_attributes(self):
        span = self.start_span("span")
        try:
            raise RuntimeError("bim")
        except RuntimeError as e:
            span.record_exception(e, {"foo": "bar"})
        span.finish()

        span.assert_span_event_count(1)
        span.assert_span_event_attributes(
            0, {"exception.type": "builtins.RuntimeError", "exception.message": "bim", "foo": "bar"}
        )

    @mock.patch("ddtrace._trace.span.log")
    def test_span_record_exception_with_invalid_attributes(self, span_log):
        span = self.start_span("span")
        try:
            raise RuntimeError("bim")
        except RuntimeError as e:
            span.record_exception(
                e, {"foo": "bar", "toto": ["titi", 1], "bim": 2**100, "tata": [[1]], "tutu": {"a": "b"}}
            )
        span.finish()

        span.assert_span_event_count(1)
        span.assert_span_event_attributes(
            0, {"exception.type": "builtins.RuntimeError", "exception.message": "bim", "foo": "bar"}
        )
        expected_calls = [
            mock.call("record_exception: Attribute %s must be a string, number, or boolean: %s.", "tutu", {"a": "b"}),
            mock.call("record_exception: Attribute %s array must be homogeneous: %s.", "toto", ["titi", 1]),
            mock.call("record_exception: List values %s must be string, number, or boolean: %s.", "tata", [[1]]),
            mock.call(
                "record_exception: Attribute %s must be within the range of a signed 64-bit integer: %s.",
                "bim",
                2**100,
            ),
        ]
        span_log.warning.assert_has_calls(expected_calls, any_order=True)
        assert span_log.warning.call_count == 4

    def test_service_entry_span(self):
        parent = self.start_span("parent", service="service1")
        child1 = self.start_span("child1", service="service1", child_of=parent)
        child2 = self.start_span("child2", service="service2", child_of=parent)

        assert parent._service_entry_span is parent
        assert child1._service_entry_span is parent
        assert child2._service_entry_span is child2

        # Renaming the service does not change the service entry span
        child1.service = "service3"
        assert child1._service_entry_span is parent

        # Service entry span only works for the immediate parent
        grandchild = self.start_span("grandchild", service="service1", child_of=child2)
        assert grandchild._service_entry_span is grandchild


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


def test_span_trace_id_preconditions():
    """Test that trace_id accepts None or generates random value for invalid types.

    trace_id no longer raises TypeError for invalid types.
    Instead, SpanData.__new__ generates a random ID when extraction fails.
    """
    # None is valid - generates random ID
    span1 = Span("test", trace_id=None)
    assert isinstance(span1.trace_id, int)
    assert span1.trace_id > 0

    # Invalid type generates random ID instead of raising
    span2 = Span("test", trace_id="foo")
    assert isinstance(span2.trace_id, int)
    assert span2.trace_id > 0


def test_span_parent_id_preconditions():
    """Test that parent_id with invalid types is silently ignored (defaults to None).

    parent_id no longer raises TypeError for invalid types.
    Instead, SpanData.__new__ defaults to None when extraction fails.
    """
    # None is valid - sets parent_id to None
    span1 = Span("test", parent_id=None)
    assert span1.parent_id is None

    # Invalid type silently ignored, defaults to None
    span2 = Span("test", parent_id="foo")
    assert span2.parent_id is None

    # Valid int is used
    span3 = Span("test", parent_id=12345)
    assert span3.parent_id == 12345


def test_span_id_preconditions():
    """Test that invalid span_id types generate a random ID instead of raising"""
    # None should generate random ID
    s1 = Span("test", span_id=None)
    assert isinstance(s1.span_id, int)
    assert s1.span_id > 0

    # Invalid type (string) should generate random ID, not raise
    s2 = Span("test", span_id="foo")
    assert isinstance(s2.span_id, int)
    assert s2.span_id > 0

    # Valid int should be used
    s3 = Span("test", span_id=12345)
    assert s3.span_id == 12345


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
