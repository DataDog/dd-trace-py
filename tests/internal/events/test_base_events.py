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
class TestEvent(Event):
    event_name = "test.event"


@dataclass
class TestEventWithAttributes(Event):
    event_name = "test.event"
    foo: str
    bar: int


def test_dispatch_event():
    """Test that dispatch_event triggers listeners for a basic event."""

    called = []

    def on_event(event_instance: TestEvent):
        called.append(event_instance.event_name)

    core.on(TestEvent.event_name, on_event)
    core.dispatch_event(TestEvent())

    assert called == [TestEvent.event_name]


def test_dispatch_event_using_attributes():
    """Test that event attributes are passed to listeners when dispatched."""

    called = []

    def on_event(event_instance: TestEventWithAttributes):
        called.append(event_instance.foo)
        called.append(event_instance.bar)

    core.on(TestEventWithAttributes.event_name, on_event)
    core.dispatch_event(TestEventWithAttributes(foo="test", bar=0))

    assert called == ["test", 0]


def test_dispatch_event_missing_attribute():
    """Test that creating an event with a missing required field raises TypeError."""

    called = []

    def on_event(event_instance: TestEventWithAttributes):
        called.append(event_instance.foo)
        called.append(event_instance.bar)

    core.on(TestEventWithAttributes.event_name, on_event)
    with pytest.raises(TypeError):
        core.dispatch_event(TestEventWithAttributes(foo="test"))

    assert called == []
