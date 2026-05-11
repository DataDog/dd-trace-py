from dataclasses import dataclass

import pytest

from ddtrace.internal import core
from ddtrace.internal.core import event_hub
from ddtrace.internal.core.events import Event


@pytest.fixture(autouse=True)
def reset_event_hub():
    """Reset event hub after each test to prevent listener leakage between tests."""
    yield
    event_hub.reset()


@dataclass
class BaseEvent(Event):
    event_name = "test.event"


@dataclass
class BaseEventWithAttributes(Event):
    event_name = "test.event"
    foo: str
    bar: int


def test_dispatch_event():
    """Test that dispatch_event triggers listeners for a basic event."""

    called = []

    def on_event(event_instance: BaseEvent):
        called.append(event_instance.event_name)

    core.on(BaseEvent.event_name, on_event)
    core.dispatch_event(BaseEvent())

    assert called == [BaseEvent.event_name], (
        "dispatching an event should call the handler once with the event name; got %r" % (called,)
    )


def test_dispatch_event_using_attributes():
    """Test that event attributes are passed to listeners when dispatched."""

    called = []

    def on_event(event_instance: BaseEventWithAttributes):
        called.append(event_instance.foo)
        called.append(event_instance.bar)

    core.on(BaseEventWithAttributes.event_name, on_event)
    core.dispatch_event(BaseEventWithAttributes(foo="test", bar=0))

    assert called == ["test", 0], "event attributes are wrongy populated; got %r" % (called,)


def test_dispatch_event_missing_attribute():
    """Test that creating an event with a missing required field raises TypeError."""

    called = []

    def on_event(event_instance: BaseEventWithAttributes):
        called.append(event_instance.foo)
        called.append(event_instance.bar)

    core.on(BaseEventWithAttributes.event_name, on_event)
    with pytest.raises(TypeError):
        core.dispatch_event(BaseEventWithAttributes(foo="test"))  # pyright: ignore[reportCallIssue]

    assert called == [], "event should not be dispatched when required event args are missing; got %r" % (called,)
