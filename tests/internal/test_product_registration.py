from dataclasses import dataclass
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
from ddtrace.internal.product_registration import ProductRegistration
from ddtrace.internal.product_registration import SubscriberBundle


@dataclass
class RegistrationEvent(Event):
    event_name = "test.product_registration"


def test_registration_is_lazy_and_selects_only_requested_bundle() -> None:
    with patch("ddtrace.internal.product_registration.import_module") as load:
        registration = ProductRegistration(
            name="example",
            subscriber_bundles={
                "web_framework": SubscriberBundle("example.web", ("RequestSubscriber", "InspectSubscriber")),
                "llm": SubscriberBundle("example.llm", ("LlmSubscriber",)),
            },
        )
        load.assert_not_called()
        registration.register("web_framework")
        load.assert_called_once_with("example.web")
        load.return_value.RequestSubscriber.register.assert_called_once_with()
        load.return_value.InspectSubscriber.register.assert_called_once_with()
        load.return_value.LlmSubscriber.register.assert_not_called()

        load.reset_mock()
        with pytest.raises(KeyError, match="unknown"):
            registration.register("unknown")
        load.assert_not_called()


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
    registration = ProductRegistration(name="example", subscriber_bundles={"web_framework": bundle})
    try:
        with patch("ddtrace.internal.product_registration.import_module", return_value=module):
            registration.register("web_framework")
            registration.register("web_framework")
            event = RegistrationEvent()
            core.dispatch_event(event)
            assert calls == [event]

            RequestSubscriber.unregister()
            registration.register("web_framework")
            core.dispatch_event(event)
            assert calls == [event, event]
    finally:
        RequestSubscriber.unregister()


def test_register_all_and_unregister_in_reverse_order(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    for name in ("example.web", "example.llm"):
        module = ModuleType(name)
        subscriber = Mock()
        subscriber.register.side_effect = lambda name=name: calls.append("register:" + name)
        subscriber.unregister.side_effect = lambda name=name: calls.append("unregister:" + name)
        setattr(module, "RequestSubscriber", subscriber)
        monkeypatch.setitem(sys.modules, name, module)
    registration = ProductRegistration(
        name="example",
        subscriber_bundles={
            "web_framework": SubscriberBundle("example.web", ("RequestSubscriber",)),
            "llm": SubscriberBundle("example.llm", ("RequestSubscriber",)),
        },
    )
    registration.register()
    registration.unregister()
    assert calls == [
        "register:example.web",
        "register:example.llm",
        "unregister:example.llm",
        "unregister:example.web",
    ]


def test_unregister_unused_bundle_does_not_import() -> None:
    registration = ProductRegistration(
        name="unused",
        subscriber_bundles={"web_framework": SubscriberBundle("unused_product.subscribers", ("Subscriber",))},
    )
    with patch("ddtrace.internal.product_registration.import_module") as load:
        registration.unregister()
        load.assert_not_called()


def test_missing_subscriber_does_not_partially_register() -> None:
    module = ModuleType("example.web")
    subscriber = Mock()
    setattr(module, "RequestSubscriber", subscriber)
    bundle = SubscriberBundle("example.web", ("RequestSubscriber", "MissingSubscriber"))
    with patch("ddtrace.internal.product_registration.import_module", return_value=module):
        with pytest.raises(AttributeError, match="MissingSubscriber"):
            bundle.register()
    subscriber.register.assert_not_called()


def test_unregister_bundle_in_reverse_order(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("example.web")
    calls: list[str] = []
    for name in ("RequestSubscriber", "InspectSubscriber"):
        subscriber = Mock()
        subscriber.unregister.side_effect = lambda name=name: calls.append(name)
        setattr(module, name, subscriber)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    SubscriberBundle(module.__name__, ("RequestSubscriber", "InspectSubscriber")).unregister()
    assert calls == ["InspectSubscriber", "RequestSubscriber"]


def test_product_declaration_is_a_snapshot() -> None:
    bundles = {"web_framework": SubscriberBundle("example.web", ("RequestSubscriber",))}
    registration = ProductRegistration(name="example", subscriber_bundles=bundles)
    bundles.clear()
    assert "web_framework" in registration.subscriber_bundles
    assert registration.subscriber_bundles != bundles


def test_product_failure_propagates_to_lifecycle() -> None:
    bundle = SubscriberBundle("example.web", ("RequestSubscriber",))
    with patch("ddtrace.internal.product_registration.import_module", side_effect=ImportError("unavailable")):
        with pytest.raises(ImportError, match="unavailable"):
            ProductRegistration(name="example", subscriber_bundles={"web_framework": bundle}).register()


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
        products.append(
            ProductRegistration(
                name=name,
                subscriber_bundles={"web_framework": SubscriberBundle(module.__name__, (subscriber.__name__,))},
            )
        )
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
