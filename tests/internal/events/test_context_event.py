from types import TracebackType
from typing import Optional
from typing import Tuple

import pytest

from ddtrace.constants import SPAN_KIND
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.core import event_hub
from ddtrace.internal.core.events import ContextEvent


@pytest.fixture(autouse=True)
def reset_event_hub():
    """Reset event hub after each test to prevent listener leakage between tests."""
    yield
    event_hub.reset()


def test_non_span_creating_context_event(test_spans):
    """Test that a ContextEvent with _start_span=False doesn't create a span"""

    class TestContextEvent(ContextEvent):
        event_name = "test.event"
        _start_span = False
        _stop_span = False

        def __new__(cls):
            return cls.create()

    core.context_with_event(TestContextEvent())
    test_spans.assert_span_count(0)


def test_incomplete_span_creating_context_event():
    """Test that a ContextEvent without required span attributes raises an AttributeError"""

    class TestContextEvent(ContextEvent):
        event_name = "test.event"

    with pytest.raises(AttributeError):
        core.context_with_event(TestContextEvent())


def test_context_event_double_dispatch():
    """Test that using the same ContextEvent twice calls the handler twice
    but only registers the listener once (no duplicate registrations)
    """
    called = []

    class TestContextEvent(ContextEvent):
        event_name = "test.event"
        _start_span = False
        _stop_span = False

        def __new__(cls):
            return cls.create()

        @classmethod
        def _on_context_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
            called.append("event_called")

    with core.context_with_event(TestContextEvent()):
        pass
    with core.context_with_event(TestContextEvent()):
        pass

    assert called == ["event_called", "event_called"]

    # Ensure that we register test.event only once
    from ddtrace.internal.core.event_hub import _listeners

    assert len(_listeners[f"context.started.{TestContextEvent.event_name}"].values()) == 1
    assert len(_listeners[f"context.ended.{TestContextEvent.event_name}"].values()) == 1


def test_basic_context_event(test_spans):
    """Test that a basic ContextEvent creates a span with the correct name"""

    class TestContextEvent(ContextEvent):
        event_name = "test.event"
        span_kind = "kind"
        component = "component"
        span_type = "type"

        def __new__(cls):
            return cls.create(span_name="test")

    with core.context_with_event(TestContextEvent()):
        pass

    test_spans.assert_span_count(1)
    span = test_spans.spans[0]
    assert span.name == "test"


def test_basic_context_event_with_default_tags(test_spans):
    """Test that a ContextEvent with get_default_tags() creates a span with the correct default tags"""

    class TestContextEvent(ContextEvent):
        event_name = "test.event"
        span_kind = "kind"
        component = "component"
        span_type = "type"

        @classmethod
        def get_default_tags(cls):
            return {
                COMPONENT: cls.component,
                SPAN_KIND: cls.span_kind,
            }

        def __new__(cls):
            return cls.create(span_name="test")

    with core.context_with_event(TestContextEvent()):
        pass

    test_spans.assert_span_count(1)
    span = test_spans.spans[0]
    assert span.name == "test"
    assert span._meta[COMPONENT] == "component"
    assert span._meta[SPAN_KIND] == "kind"


def test_basic_context_event_with_custom_tags(test_spans):
    """Test that a ContextEvent can add custom tags on top of default tags"""

    class TestContextEvent(ContextEvent):
        event_name = "test.event"
        span_kind = "kind"
        component = "component"
        span_type = "type"

        @classmethod
        def get_default_tags(cls):
            return {
                COMPONENT: cls.component,
                SPAN_KIND: cls.span_kind,
            }

        def __new__(cls, foo, tags=None):
            instance_tags = {"foo": foo, **(tags or {})}
            return cls.create(span_name="test", tags=instance_tags)

    with core.context_with_event(TestContextEvent(foo="bar", tags={"additional": "tags"})):
        pass

    test_spans.assert_span_count(1)
    span = test_spans.spans[0]
    assert span.name == "test"
    assert span._meta[COMPONENT] == "component"
    assert span._meta[SPAN_KIND] == "kind"
    assert span._meta["foo"] == "bar"
    assert span._meta["additional"] == "tags"


def test_context_event_on_context_started_ended(test_spans):
    """Test that _on_context_started and _on_context_ended hooks are called
    and can access context items to set span tags
    """

    class TestContextEvent(ContextEvent):
        event_name = "test.event"
        span_kind = "kind"
        component = "component"
        span_type = "type"

        def __new__(cls, foo, bar):
            return cls.create(span_name="test", foo=foo, bar=bar)

        @classmethod
        def _on_context_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
            span = ctx.span
            span._set_tag_str("foo", ctx.get_item("foo"))

        @classmethod
        def _on_context_ended(
            cls,
            ctx: core.ExecutionContext,
            exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
        ) -> None:
            span = ctx.span
            span._set_tag_str("bar", ctx.get_item("bar"))

    with core.context_with_event(TestContextEvent(foo="toto", bar="tata")):
        pass

    test_spans.assert_span_count(1)
    span = test_spans.spans[0]
    assert span.name == "test"
    assert span._meta["foo"] == "toto"
    assert span._meta["bar"] == "tata"


def test_context_event_inheriting_context_event(test_spans):
    """Test that a ContextEvent can inherit from another ContextEvent
    and extend its functionality while preserving parent hooks
    """

    class TestContextEvent(ContextEvent):
        event_name = "test.event"
        span_kind = "kind"
        component = "component"
        span_type = "type"

        @classmethod
        def get_default_tags(cls):
            return {COMPONENT: cls.component, SPAN_KIND: cls.span_kind}

        def __new__(cls, foo, bar):
            return cls.create(span_name="test", foo=foo, bar=bar)

        @classmethod
        def _on_context_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
            span = ctx.span
            span._set_tag_str("foo", ctx.get_item("foo"))

        @classmethod
        def _on_context_ended(
            cls,
            ctx: core.ExecutionContext,
            exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
        ) -> None:
            span = ctx.span
            span._set_tag_str("bar", ctx.get_item("bar"))

    class AnotherTestContextEvent(TestContextEvent):
        event_name = "another.test.event"

        def __new__(cls, foo, bar, new_field):
            return cls.create(span_name="test", foo=foo, bar=bar, new_field=new_field)

        @classmethod
        def _on_context_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
            span = ctx.span
            span._set_tag_str("new_field", ctx.get_item("new_field"))

    with core.context_with_event(AnotherTestContextEvent(foo="toto", bar="tata", new_field="hello")):
        pass

    test_spans.assert_span_count(1)
    span = test_spans.spans[0]
    assert span.name == "test"
    assert span._meta["foo"] == "toto"
    assert span._meta["bar"] == "tata"
    assert span._meta["new_field"] == "hello"
