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
    class ContextEvent(Event):
        event_name = "test.event"

    def on_context_started(ctx: core.ExecutionContext):
        called.append(f"{ContextEvent.event_name}.started")

    def on_context_ended(ctx: core.ExecutionContext, err_info: Any):
        called.append(f"{ContextEvent.event_name}.ended")

    core.on(f"context.started.{ContextEvent.event_name}", on_context_started)
    core.on(f"context.ended.{ContextEvent.event_name}", on_context_ended)

    with core.context_with_event(ContextEvent()):
        pass

    assert called == [f"{ContextEvent.event_name}.started", f"{ContextEvent.event_name}.ended"], (
        "event should trigger started then ended handlers in order; got %r" % (called,)
    )


@pytest.mark.skipif(sys.version_info < (3, 10), reason="Requires Python 3.10+")
def test_context_event_enforce_kwargs_error():
    """Test that missing required fields raise TypeError.
    On Python 3.9, we create a default value to every attributes because kw_only
    is not available in dataclass field. Therefore we skip the test
    """
    called = []

    @dataclass
    class ContextEvent(Event):
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

    core.on(f"context.started.{ContextEvent.event_name}", on_context_started)
    core.on(f"context.ended.{ContextEvent.event_name}", on_context_ended)

    with pytest.raises(TypeError):
        with core.context_with_event(ContextEvent(foo="toto")):  # pyright: ignore[reportCallIssue]
            pass

    assert called == [], "event should not be dispatched when required event args are missing; got %r" % (called,)


def test_context_event_event_field():
    """Test that event_field with in_context=True stores data in context."""
    called = []

    @dataclass
    class ContextEvent(Event):
        event_name = "test.event"
        foo: str = event_field()
        with_default: str = event_field(default="test")
        not_in_context: InitVar[int] = event_field()

        def __post_init__(self, not_in_context):
            called.append(not_in_context)

    def on_context_started(ctx: core.ExecutionContext) -> None:
        event: ContextEvent = ctx.event
        called.append(event.foo)
        called.append(event.with_default)

        assert getattr(event, "not_in_context", None) is None, (
            "InitVar field marked out of context should not be present on context event"
        )

    core.on(f"context.started.{ContextEvent.event_name}", on_context_started)

    with core.context_with_event(ContextEvent(foo="toto", not_in_context=0)):
        pass

    assert called == [0, "toto", "test"], (
        "event field values should include InitVar payload and context attrs; got %r" % (called,)
    )


def test_context_with_event_context_name_override():
    """Test that context_name_override controls started/ended event ids."""

    called = []

    @dataclass
    class ContextEvent(Event):
        event_name = "test.event.default_name"

    override_name = "test.event.override_name"

    def on_override_started(ctx: core.ExecutionContext):
        called.append("override_started")

    def on_override_ended(ctx: core.ExecutionContext, err_info: Any):
        called.append("override_ended")

    def on_default_started(ctx: core.ExecutionContext):
        called.append("default_started")

    def on_default_ended(ctx: core.ExecutionContext, err_info: Any):
        called.append("default_ended")

    core.on(f"context.started.{override_name}", on_override_started)
    core.on(f"context.ended.{override_name}", on_override_ended)
    core.on(f"context.started.{ContextEvent.event_name}", on_default_started)
    core.on(f"context.ended.{ContextEvent.event_name}", on_default_ended)

    with core.context_with_event(ContextEvent(), context_name_override=override_name):
        pass

    assert called == ["override_started", "override_ended"], (
        "override context name should be used for lifecycle dispatch; got %r" % (called,)
    )


def test_context_with_event_dispatch_end_event_false_no_auto_end():
    """Test that context.started dispatches and context.ended can be suppressed."""

    called = []

    @dataclass
    class ContextEvent(Event):
        event_name = "test.event.no_auto_end"

    def on_context_started(ctx: core.ExecutionContext):
        called.append("started")

    def on_context_ended(ctx: core.ExecutionContext, err_info: Any):
        called.append("ended")

    core.on(f"context.started.{ContextEvent.event_name}", on_context_started)
    core.on(f"context.ended.{ContextEvent.event_name}", on_context_ended)

    with core.context_with_event(ContextEvent(), dispatch_end_event=False):
        pass

    assert called == ["started"], (
        "suppressed context_with_event should dispatch started but not auto-dispatch ended; got %r" % (called,)
    )
