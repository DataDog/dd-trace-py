from dataclasses import dataclass
import inspect
import sys
from types import ModuleType
from types import TracebackType
from typing import Optional
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from ddtrace.internal import core
from ddtrace.internal.core.events import Event
from ddtrace.internal.core.subscriber import ContextSubscriber
from ddtrace.internal.core.subscriber import Subscriber
from ddtrace.internal.core.subscriber import SubscriberBundle
from ddtrace.internal.core.subscriber import requires_subscribers
from ddtrace.internal.products import ProductManager


@dataclass
class RegistrationEvent(Event):
    event_name = "test.product_registration"


def test_bundle_registration_is_lazy() -> None:
    with patch("ddtrace.internal.core.subscriber.import_module") as load:
        bundle = SubscriberBundle("example.web", ("RequestSubscriber", "InspectSubscriber"))
        load.assert_not_called()
        bundle.register()
        load.assert_called_once_with("example.web")
        load.return_value.RequestSubscriber.register.assert_called_once_with()
        load.return_value.InspectSubscriber.register.assert_called_once_with()


def test_shared_bundle_is_idempotent_and_can_restore_listeners() -> None:
    calls = []

    class RequestSubscriber(Subscriber):
        auto_register = False
        event_names = (RegistrationEvent.event_name,)

        @classmethod
        def on_event(cls, event: RegistrationEvent) -> None:
            calls.append(event)

    module = ModuleType("example.web")
    setattr(module, "RequestSubscriber", RequestSubscriber)
    bundle = SubscriberBundle("example.web", ("RequestSubscriber",))
    try:
        with patch("ddtrace.internal.core.subscriber.import_module", return_value=module):
            bundle.register()
            bundle.register()
            event = RegistrationEvent()
            core.dispatch_event(event)
            assert calls == [event]

            RequestSubscriber.unregister()
            bundle.register()
            core.dispatch_event(event)
            assert calls == [event, event]
    finally:
        RequestSubscriber.unregister()


def test_unregister_unused_bundle_does_not_import() -> None:
    bundle = SubscriberBundle("unused_product.subscribers", ("Subscriber",))
    with patch("ddtrace.internal.core.subscriber.import_module") as load:
        bundle.unregister()
        load.assert_not_called()


def test_missing_subscriber_does_not_partially_register() -> None:
    module = ModuleType("example.web")
    subscriber = Mock()
    setattr(module, "RequestSubscriber", subscriber)
    bundle = SubscriberBundle("example.web", ("RequestSubscriber", "MissingSubscriber"))
    with patch("ddtrace.internal.core.subscriber.import_module", return_value=module):
        with pytest.raises(AttributeError, match="MissingSubscriber"):
            bundle.register()
    subscriber.register.assert_not_called()


def test_unregister_bundle_removes_all_subscribers(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("example.web")
    calls: list[str] = []
    for name in ("RequestSubscriber", "InspectSubscriber"):
        subscriber = Mock()
        subscriber.unregister.side_effect = lambda name=name: calls.append(name)
        setattr(module, name, subscriber)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    SubscriberBundle(module.__name__, ("RequestSubscriber", "InspectSubscriber")).unregister()
    assert sorted(calls) == ["InspectSubscriber", "RequestSubscriber"]


def test_bundle_import_failure_propagates_to_caller() -> None:
    bundle = SubscriberBundle("example.web", ("RequestSubscriber",))
    with patch("ddtrace.internal.core.subscriber.import_module", side_effect=ImportError("unavailable")):
        with pytest.raises(ImportError, match="unavailable"):
            bundle.register()


def test_products_can_share_events_without_sharing_listener_ownership(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class TracingRequestSubscriber(Subscriber):
        auto_register = False
        event_names = (RegistrationEvent.event_name,)

        @classmethod
        def on_event(cls, event: RegistrationEvent) -> None:
            calls.append("tracing")

    class SecurityRequestSubscriber(ContextSubscriber[RegistrationEvent]):
        auto_register = False
        event_names = (RegistrationEvent.event_name,)

        @classmethod
        def on_started(cls, ctx: core.ExecutionContext[RegistrationEvent]) -> None:
            calls.append("security:start")

        @classmethod
        def on_ended(
            cls,
            ctx: core.ExecutionContext[RegistrationEvent],
            exc_info: tuple[Optional[type[BaseException]], Optional[BaseException], Optional[TracebackType]],
        ) -> None:
            calls.append("security:end")

    products = []
    for name, subscriber in (("tracing", TracingRequestSubscriber), ("security", SecurityRequestSubscriber)):
        module = ModuleType("example." + name)
        setattr(module, subscriber.__name__, subscriber)
        monkeypatch.setitem(sys.modules, module.__name__, module)
        products.append(SubscriberBundle(module.__name__, (subscriber.__name__,)))
    tracing, security = products
    try:
        tracing.register()
        security.register()
        security.register()
        core.dispatch_event(RegistrationEvent())
        with core.context_with_event(RegistrationEvent()):
            pass
        assert calls == ["tracing", "security:start", "security:end"]

        security.unregister()
        calls.clear()
        core.dispatch_event(RegistrationEvent())
        with core.context_with_event(RegistrationEvent()):
            pass
        assert calls == ["tracing"]
    finally:
        security.unregister()
        tracing.unregister()


@pytest.mark.parametrize("shutdown", ["stop_products", "exit_products"])
def test_product_manages_bundle_listeners_through_lifecycle(monkeypatch: pytest.MonkeyPatch, shutdown: str) -> None:
    calls: list[str] = []

    class RequestSubscriber(ContextSubscriber[RegistrationEvent]):
        auto_register = False
        event_names = (RegistrationEvent.event_name,)

        @classmethod
        def on_started(cls, ctx: core.ExecutionContext[RegistrationEvent]) -> None:
            calls.append("start")

        @classmethod
        def on_ended(
            cls,
            ctx: core.ExecutionContext[RegistrationEvent],
            exc_info: tuple[Optional[type[BaseException]], Optional[BaseException], Optional[TracebackType]],
        ) -> None:
            calls.append("end")

    module = ModuleType("example.managed_subscribers")
    setattr(module, "RequestSubscriber", RequestSubscriber)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    product = Mock()
    product.requires = []
    product.enabled.return_value = True
    product.skip_exit.return_value = False
    bundle = SubscriberBundle(module.__name__, ("RequestSubscriber",))
    product.start.side_effect = bundle.register
    manager = ProductManager()
    manager._products = [("test", product)]
    context = core.context_with_event(RegistrationEvent())

    # stop() drains the active context while its completion listener is still registered.
    def stop(join=False):
        context.__exit__(None, None, None)
        bundle.unregister()

    product.stop.side_effect = stop
    try:
        manager.start_products()
        manager.start_products()
        context.__enter__()
        assert calls == ["start"]
        getattr(manager, shutdown)()
        assert calls == ["start", "end"]
        with core.context_with_event(RegistrationEvent()):
            pass
        assert calls == ["start", "end"]
    finally:
        RequestSubscriber.unregister()


def test_setup_declaration_is_lazy_and_preserves_call_contract() -> None:
    calls: list[object] = []
    first = Mock(spec=SubscriberBundle)
    second = Mock(spec=SubscriberBundle)
    first.register.side_effect = lambda: calls.append("first")
    second.register.side_effect = lambda: calls.append("second")

    def setup(value: str, *, enabled: bool = True) -> str:
        """Install instrumentation."""
        calls.append((value, enabled))
        return value

    decorated = requires_subscribers(first, second)(setup)
    assert calls == []
    assert decorated.__name__ == setup.__name__
    assert decorated.__doc__ == setup.__doc__
    assert inspect.signature(decorated) == inspect.signature(setup)
    assert decorated("service", enabled=False) == "service"
    assert calls == ["first", "second", ("service", False)]


def test_setup_declaration_supports_constructor_and_listener_reset() -> None:
    calls = []

    class RequestSubscriber(Subscriber):
        auto_register = False
        event_names = (RegistrationEvent.event_name,)

        @classmethod
        def on_event(cls, event: RegistrationEvent) -> None:
            calls.append(event)

    module = ModuleType("example.setup")
    setattr(module, "RequestSubscriber", RequestSubscriber)
    bundle = SubscriberBundle(module.__name__, ("RequestSubscriber",))

    class Middleware:
        @requires_subscribers(bundle)
        def __init__(self, service: str) -> None:
            self.service = service

    try:
        with patch("ddtrace.internal.core.subscriber.import_module", return_value=module):
            assert Middleware("test").service == "test"
            Middleware("test")
            event = RegistrationEvent()
            core.dispatch_event(event)
            assert calls == [event]
            RequestSubscriber.unregister()
            Middleware("restored")
            core.dispatch_event(event)
            assert calls == [event, event]
    finally:
        RequestSubscriber.unregister()


def test_setup_declaration_registration_error_prevents_setup() -> None:
    setup = Mock()
    decorated = requires_subscribers(SubscriberBundle("missing.subscribers", ("Subscriber",)))(setup)
    with patch("ddtrace.internal.core.subscriber.import_module", side_effect=ImportError("unavailable")):
        with pytest.raises(ImportError, match="unavailable"):
            decorated()
    setup.assert_not_called()
