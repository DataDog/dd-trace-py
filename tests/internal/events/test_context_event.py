from types import TracebackType
from typing import Optional
from typing import Tuple

import pytest

from ddtrace.internal import core
from ddtrace.internal.core import event_hub
from ddtrace.internal.core.events import ContextEvent
from ddtrace.internal.core.events import context_event
from ddtrace.internal.core.events import event_field


@pytest.fixture(autouse=True)
def reset_event_hub():
    """Reset event hub after each test to prevent listener leakage between tests."""
    yield
    event_hub.reset()


def test_basic_context_event():
    """Test that ContextEvent triggers _on_context_started and _on_context_ended hooks."""
    called = []

    @context_event
    class TestContextEvent(ContextEvent):
        event_name = "test.event"

        @classmethod
        def _on_context_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
            called.append("started")

        @classmethod
        def _on_context_ended(
            cls,
            ctx: core.ExecutionContext,
            exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
        ) -> None:
            called.append("ended")

    with core.context_with_event(TestContextEvent()):
        pass

    assert called == ["started", "ended"]


def test_context_event_double_dispatch():
    """Test that dispatching the same context event twice calls hooks twice but registers only once."""
    called = []

    @context_event
    class TestContextEvent(ContextEvent):
        event_name = "test.event"

        @classmethod
        def _on_context_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
            called.append("started")

        @classmethod
        def _on_context_ended(
            cls,
            ctx: core.ExecutionContext,
            exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
        ) -> None:
            called.append("ended")

    with core.context_with_event(TestContextEvent()):
        pass
    with core.context_with_event(TestContextEvent()):
        pass

    assert called == ["started", "ended", "started", "ended"]

    # Ensure that we register test.event only once
    from ddtrace.internal.core.event_hub import _listeners

    assert len(_listeners[f"context.started.{TestContextEvent.event_name}"].values()) == 1
    assert len(_listeners[f"context.ended.{TestContextEvent.event_name}"].values()) == 1


def test_context_event_enforce_kwargs_error():
    """Test that missing required fields raise TypeError."""
    called = []

    @context_event
    class TestContextEvent(ContextEvent):
        event_name = "test.event"
        foo: str
        bar: int

        @classmethod
        def _on_context_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
            called.append("started")

        @classmethod
        def _on_context_ended(
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
    """Test that missing required fields raise TypeError."""
    called = []

    @context_event
    class TestContextEvent(ContextEvent):
        event_name = "test.event"
        foo: str = event_field(in_context=True)
        not_in_context: int
        with_default: str = event_field(default="test", in_context=True)

        @classmethod
        def _on_context_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
            called.append("started")
            called.append(ctx.get_item("foo"))
            called.append(ctx.get_item("with_default"))

            assert ctx.get_item("not_in_context") is None

    with core.context_with_event(TestContextEvent(foo="toto", not_in_context=0)):
        pass

    assert called == ["started", "toto", "test"]


def test_content_event_inheriting_context_event():
    """Test that child ContextEvent inherits and extends parent's hooks."""
    called = []

    @context_event
    class TestContextEvent(ContextEvent):
        event_name = "test.event"
        foo: str = event_field(in_context=True)

        @classmethod
        def _on_context_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
            called.append("base_started")

        @classmethod
        def _on_context_ended(
            cls,
            ctx: core.ExecutionContext,
            exc_info: Tuple[Optional[type], Optional[BaseException], Optional[TracebackType]],
        ) -> None:
            called.append("base_ended")

    @context_event
    class ChildTestContextEvent(TestContextEvent):
        event_name = "test.child.event"

        @classmethod
        def _on_context_started(cls, ctx: core.ExecutionContext, call_trace: bool = True, **kwargs) -> None:
            called.append("child_started")

        @classmethod
        def _on_context_ended(
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
    """Test that exception info is properly passed to _on_context_ended."""
    called = []

    @context_event
    class TestContextEvent(ContextEvent):
        event_name = "test.event"

        @classmethod
        def _on_context_ended(
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
