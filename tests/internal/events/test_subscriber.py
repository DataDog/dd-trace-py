from dataclasses import dataclass

import pytest

from ddtrace.internal import core
from ddtrace.internal.core import event_hub
from ddtrace.internal.core.events import Event
from ddtrace.internal.core.subscriber import BaseSubscriber


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


def test_base_subscriber_registers_handler():
    """Test that TestEvent automatically registers its on_event handler when dispatched."""
    called = []

    class TestSubscriber(BaseSubscriber):
        event_name = TestEvent.event_name

        @classmethod
        def on_event(cls, event_instance: TestEvent):
            called.append("event_called")

    core.dispatch_event(TestEvent())

    assert called == ["event_called"]


def test_base_event_enforce_kwargs_error():
    """Test that missing required fields raise TypeError."""
    called = []

    class TestSubscriber(BaseSubscriber):
        event_name = TestEventWithAttributes.event_name

        @classmethod
        def on_event(cls, event_instance: TestEventWithAttributes):
            called.append(event_instance.foo)

    with pytest.raises(TypeError):
        core.dispatch_event(TestEvent(foo="toto"))

    assert called == []


def test_base_event_enforce_kwargs():
    """Test that event instances with all required fields are dispatched correctly."""
    called = []

    class TestSubscriber(BaseSubscriber):
        event_name = TestEventWithAttributes.event_name

        @classmethod
        def on_event(cls, event_instance: TestEventWithAttributes):
            called.append(event_instance.foo)
            called.append(event_instance.bar)

    core.dispatch_event(TestEventWithAttributes(foo="toto", bar=1))

    assert called == ["toto", 1]


def test_base_event_compatible_with_core_api():
    """Test that Event is compatible with core API."""
    called = []

    class TestSubscriber(BaseSubscriber):
        event_name = TestEventWithAttributes.event_name

        @classmethod
        def on_event(cls, event_instance: TestEventWithAttributes):
            called.append("event_api")

    def trace_hook(event: TestEventWithAttributes):
        called.append("core_api")
        called.append(event.foo)

    core.on(TestEvent.event_name, trace_hook)

    core.dispatch_event(TestEventWithAttributes(foo="bar", bar=1))
    assert called == ["event_api", "core_api", "bar"]
