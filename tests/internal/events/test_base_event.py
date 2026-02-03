from dataclasses import dataclass

import pytest

from ddtrace.internal import core
from ddtrace.internal.core import event_hub
from ddtrace.internal.core.events import BaseEvent


@pytest.fixture(autouse=True)
def reset_event_hub():
    """Reset event hub after each test to prevent listener leakage between tests."""
    yield
    event_hub.reset()


def test_base_event_registers_handler():
    """Test that BaseEvent automatically registers its on_event handler when dispatched."""
    called = []

    @dataclass
    class TestEvent(BaseEvent):
        event_name = "test.event"

        @classmethod
        def on_event(cls, event_instance, *args):
            called.append("event_called")

    core.dispatch_event(TestEvent())

    assert called == ["event_called"]


def test_base_event_double_dispatch():
    """Test that dispatching the same event twice calls the handler twice but only registers once."""
    called = []

    @dataclass
    class TestEvent(BaseEvent):
        event_name = "test.event"

        @classmethod
        def on_event(cls, event_instance, *args):
            called.append("event_called")

    core.dispatch_event(TestEvent())
    core.dispatch_event(TestEvent())

    assert called == ["event_called", "event_called"]

    # Ensure that we register test.event only once
    from ddtrace.internal.core.event_hub import _listeners

    assert len(_listeners[TestEvent.event_name].values()) == 1


def test_base_event_additional_args():
    """Test that additional positional arguments passed to dispatch_event are forwarded to on_event."""
    called = []

    @dataclass
    class TestEvent(BaseEvent):
        event_name = "test.event"

        @classmethod
        def on_event(cls, event_instance, *args):
            called.append("event_called")
            called.append(args[0])

    core.dispatch_event(TestEvent(), "foo")
    assert called == ["event_called", "foo"]


def test_base_event_enforce_kwargs():
    """Test that event instances args enforcement"""
    called = []

    @dataclass
    class TestEvent(BaseEvent):
        event_name = "test.event"
        foo: str
        bar: int

        @classmethod
        def on_event(cls, event_instance, *args):
            called.append(event_instance.foo)
            called.append(event_instance.bar)
            called.append(args[0])

    core.dispatch_event(TestEvent(foo="toto", bar=0), "titi")
    assert called == ["toto", 0, "titi"]


def test_base_event_compatible_with_core_api():
    """Test that BaseEvent compatiblity with core API."""
    called = []

    @dataclass
    class TestEvent(BaseEvent):
        event_name = "test.event"
        foo: str

        @classmethod
        def on_event(cls, event_instance, *args):
            called.append("event_api")

    def trace_hook(event):
        called.append("core_api")
        called.append(event.foo)

    core.on(TestEvent.event_name, trace_hook)

    core.dispatch_event(TestEvent(foo="bar"))
    assert called == ["event_api", "core_api", "bar"]
