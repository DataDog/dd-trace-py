import sys
from types import TracebackType
from typing import Optional
from typing import Tuple

import pytest

from ddtrace.internal import core
from ddtrace.internal.core import event_hub
from ddtrace.internal.core.events import ContextEvent
from ddtrace.internal.core.events import context_event
from ddtrace.internal.core.events import event_field
from ddtrace.internal.core.subscriber import BaseContextSubscriber


@pytest.fixture(autouse=True)
def reset_event_hub():
    """Reset event hub after each test to prevent listener leakage between tests."""
    yield
    event_hub.reset()


def test_basic_context_event():
    """Test that ContextEvent triggers on_started and on_ended hooks via subscriber."""
    called = []

    @context_event
    class TestContextEvent(ContextEvent):
        event_name = "test.event"

    class TestSubscriber(BaseContextSubscriber):
        event_name = TestContextEvent.event_name

        @classmethod
        def on_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
            called.append("started")

        @classmethod
        def on_ended(
            cls,
            ctx: core.ExecutionContext,
            exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
        ) -> None:
            called.append("ended")

    with core.context_with_event(TestContextEvent()):
        pass

    assert called == ["started", "ended"]


@pytest.mark.skipif(sys.version_info < (3, 10), reason="Requires Python 3.10+")
def test_context_event_enforce_kwargs_error():
    """Test that missing required fields raise TypeError.
    On Python 3.9, we create a default value to every attributes because kw_only
    is not available in dataclass field. Therefore we skip the test
    """
    called = []

    @context_event
    class TestContextEvent(ContextEvent):
        event_name = "test.event"
        foo: str
        bar: int

    class TestSubscriber(BaseContextSubscriber):
        event_name = TestContextEvent.event_name

        @classmethod
        def on_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
            called.append("started")

        @classmethod
        def on_ended(
            cls,
            ctx: core.ExecutionContext,
            exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
        ) -> None:
            called.append("ended")

    with pytest.raises(TypeError):
        with core.context_with_event(TestContextEvent(foo="toto")):
            pass

    assert called == []


def test_context_event_event_field():
    """Test that event_field with in_context=True stores data in context."""
    called = []

    @context_event
    class TestContextEvent(ContextEvent):
        event_name = "test.event"
        foo: str = event_field(in_context=True)
        not_in_context: int
        with_default: str = event_field(default="test", in_context=True)

    class TestSubscriber(BaseContextSubscriber):
        event_name = TestContextEvent.event_name

        @classmethod
        def on_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
            called.append("started")
            called.append(ctx.get_item("foo"))
            called.append(ctx.get_item("with_default"))

            assert ctx.get_item("not_in_context") is None

    with core.context_with_event(TestContextEvent(foo="toto", not_in_context=0)):
        pass

    assert called == ["started", "toto", "test"]


def test_content_event_inheriting_context_event():
    """Test that child ContextEvent inherits and extends parent's hooks via subscribers."""
    called = []

    @context_event
    class TestContextEvent(ContextEvent):
        event_name = "test.event"
        foo: str = event_field(in_context=True)

    class TestSubscriber(BaseContextSubscriber):
        event_name = TestContextEvent.event_name

        @classmethod
        def on_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
            called.append("base_started")

        @classmethod
        def on_ended(
            cls,
            ctx: core.ExecutionContext,
            exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
        ) -> None:
            called.append("base_ended")

    @context_event
    class ChildTestContextEvent(TestContextEvent):
        event_name = "test.child.event"

    class ChildTestSubscriber(TestSubscriber):
        event_name = ChildTestContextEvent.event_name

        @classmethod
        def on_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
            called.append("child_started")

        @classmethod
        def on_ended(
            cls,
            ctx: core.ExecutionContext,
            exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
        ) -> None:
            called.append("child_ended")
            called.append(ctx.get_item("foo"))

    with core.context_with_event(ChildTestContextEvent(foo="toto")):
        pass

    assert called == ["base_started", "child_started", "base_ended", "child_ended", "toto"]


def test_context_event_with_exception():
    """Test that exception info is properly passed to on_ended via subscriber."""
    called = []

    @context_event
    class TestContextEvent(ContextEvent):
        event_name = "test.event"

    class TestSubscriber(BaseContextSubscriber):
        event_name = TestContextEvent.event_name

        @classmethod
        def on_ended(
            cls,
            ctx: core.ExecutionContext,
            exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
        ) -> None:
            called.append(exc_info)

    with pytest.raises(ValueError):
        with core.context_with_event(TestContextEvent()):
            raise ValueError("test error")

    assert called[0][0] == ValueError
    assert str(called[0][1]) == "test error"


def test_context_event_compatible_with_core_api():
    """Test that the subscriber pattern is compatible with traditional core.on() listeners."""
    called = []

    @context_event
    class TestContextEvent(ContextEvent):
        event_name = "test.compatibility"
        data: str = event_field(in_context=True)

    class TestSubscriber(BaseContextSubscriber):
        event_name = TestContextEvent.event_name

        @classmethod
        def on_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
            called.append("subscriber_started")

        @classmethod
        def on_ended(
            cls,
            ctx: core.ExecutionContext,
            exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
        ) -> None:
            called.append("subscriber_ended")

    # Register traditional core.on() listeners alongside the subscriber
    def on_traditional_started(ctx):
        called.append("core_api_started")

    def on_traditional_ended(ctx, exc_info):
        called.append("core_api_ended")
        called.append(ctx.get_item("data"))

    core.on(f"context.started.{TestContextEvent.event_name}", on_traditional_started)
    core.on(f"context.ended.{TestContextEvent.event_name}", on_traditional_ended)

    with core.context_with_event(TestContextEvent(data="test_value")):
        pass

    assert called == ["subscriber_started", "core_api_started", "subscriber_ended", "core_api_ended", "test_value"]
