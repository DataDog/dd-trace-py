from dataclasses import InitVar
from dataclasses import dataclass
import sys
from typing import Any

import pytest

from ddtrace.internal import core
from ddtrace.internal.core import event_hub
from ddtrace.internal.core.events import Event
from ddtrace.internal.core.events import event_field


@pytest.fixture(autouse=True)
def reset_event_hub():
    """Reset event hub after each test to prevent listener leakage between tests."""
    yield
    event_hub.reset()


def test_basic_context_event():
    """Test that context start and end listeners are called for a context event."""

    called = []

    @dataclass
    class TestContextEvent(Event):
        event_name = "test.event"

    def on_context_started(ctx: core.ExecutionContext):
        called.append(f"{TestContextEvent.event_name}.started")

    def on_context_ended(ctx: core.ExecutionContext, err_info: Any):
        called.append(f"{TestContextEvent.event_name}.ended")

    core.on(f"context.started.{TestContextEvent.event_name}", on_context_started)
    core.on(f"context.ended.{TestContextEvent.event_name}", on_context_ended)

    with core.context_with_event(TestContextEvent()):
        pass

    assert called == [f"{TestContextEvent.event_name}.started", f"{TestContextEvent.event_name}.ended"]


@pytest.mark.skipif(sys.version_info < (3, 10), reason="Requires Python 3.10+")
def test_context_event_enforce_kwargs_error():
    """Test that missing required fields raise TypeError.
    On Python 3.9, we create a default value to every attributes because kw_only
    is not available in dataclass field. Therefore we skip the test
    """
    called = []

    @dataclass
    class TestContextEvent(Event):
        event_name = "test.event"
        foo: str = event_field()
        bar: int = event_field()

    def on_context_started(cls, ctx: core.ExecutionContext) -> None:
        called.append("started")

    def on_context_ended(
        ctx: core.ExecutionContext,
        exc_info: Any,
    ) -> None:
        called.append("ended")

    core.on(f"context.started.{TestContextEvent.event_name}", on_context_started)
    core.on(f"context.ended.{TestContextEvent.event_name}", on_context_ended)

    with pytest.raises(TypeError):
        with core.context_with_event(TestContextEvent(foo="toto")):
            pass

    assert called == []


def test_context_event_event_field():
    """Test that event_field with in_context=True stores data in context."""
    called = []

    @dataclass
    class TestContextEvent(Event):
        event_name = "test.event"
        foo: str = event_field()
        with_default: str = event_field(default="test")
        not_in_context: InitVar[int] = event_field()

        def __post_init__(self, not_in_context):
            called.append(not_in_context)

    def on_context_started(ctx: core.ExecutionContext) -> None:
        event: TestContextEvent = ctx.event
        called.append(event.foo)
        called.append(event.with_default)

        assert getattr(event, "not_in_context", None) is None

    core.on(f"context.started.{TestContextEvent.event_name}", on_context_started)

    with core.context_with_event(TestContextEvent(foo="toto", not_in_context=0)):
        pass

    assert called == [0, "toto", "test"]


def test_content_event_inheritance():
    """Test that child ContextEvent inherits and extends parent's hooks via subscribers."""
    called = []

    @dataclass
    class TestContextEvent(Event):
        event_name = "test.event"
        foo: str = event_field()

    @dataclass
    class ChildTestContextEvent(TestContextEvent):
        event_name = "test.child.event"

    def on_context_started_child(ctx: core.ExecutionContext):
        event: ChildTestContextEvent = ctx.event
        called.append(event.foo)

    core.on(f"context.started.{ChildTestContextEvent.event_name}", on_context_started_child)

    with core.context_with_event(ChildTestContextEvent(foo="toto")):
        pass

    assert called == ["toto"]
