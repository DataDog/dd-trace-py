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

    event_name = ""

    def on_event(event_instance: TestEvent):
        nonlocal event_name
        event_name = event_instance.event_name

    core.on(TestEvent.event_name, on_event)
    core.dispatch_event(TestEvent())

    assert event_name == TestEvent.event_name


def test_dispatch_event_using_attributes():
    """Test that event attributes are passed to listeners when dispatched."""

    foo = ""
    bar = -1

    def on_event(event_instance: TestEventWithAttributes):
        nonlocal foo, bar
        foo = event_instance.foo
        bar = event_instance.bar

    core.on(TestEventWithAttributes.event_name, on_event)
    core.dispatch_event(TestEventWithAttributes(foo="test", bar=0))

    assert foo == "test"
    assert bar == 0


def test_dispatch_event_missing_attribute():
    """Test that creating an event with a missing required field raises TypeError."""

    called = False

    def on_event(event_instance: TestEventWithAttributes):
        nonlocal called
        called = True

    core.on(TestEventWithAttributes.event_name, on_event)
    with pytest.raises(TypeError):
        core.dispatch_event(TestEventWithAttributes(foo="test"))

    assert called is False
