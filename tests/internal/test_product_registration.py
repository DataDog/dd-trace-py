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
from ddtrace.internal.products import ProductManager
from ddtrace.internal.products import SubscriberBundle


@dataclass
class RegistrationEvent(Event):
    event_name = "test.product_registration"


def test_bundle_registration_is_lazy() -> None:
    with patch("ddtrace.internal.products.import_module") as load:
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
        with patch("ddtrace.internal.products.import_module", return_value=module):
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
    with patch("ddtrace.internal.products.import_module") as load:
        bundle.unregister()
        load.assert_not_called()


def test_missing_subscriber_does_not_partially_register() -> None:
    module = ModuleType("example.web")
    subscriber = Mock()
    setattr(module, "RequestSubscriber", subscriber)
    bundle = SubscriberBundle("example.web", ("RequestSubscriber", "MissingSubscriber"))
    with patch("ddtrace.internal.products.import_module", return_value=module):
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
    with patch("ddtrace.internal.products.import_module", side_effect=ImportError("unavailable")):
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


def test_tracing_bundle_registration_preserves_request_completion(tracer, test_spans) -> None:
    from ddtrace._trace.product import WEB_FRAMEWORK_SUBSCRIBERS
    from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
    from ddtrace.internal.settings._config import Config
    from ddtrace.internal.settings.integration import IntegrationConfig
    from ddtrace.internal.span_bus import span_from_context

    WEB_FRAMEWORK_SUBSCRIBERS.register()
    WEB_FRAMEWORK_SUBSCRIBERS.register()
    event = WebFrameworkRequestEvent(
        http_operation="test.request",
        component="test",
        integration_config=IntegrationConfig(Config(), "test"),
        request_url="http://localhost/",
        request_method="GET",
        request_headers={},
        headers_case_sensitive=True,
    )
    with core.context_with_event(event) as ctx:
        span = span_from_context(ctx)
        assert not span.finished
        event.response_status_code = 201
    assert span.finished
    assert span.get_tag("http.status_code") == "201"
    assert test_spans.pop() == [span]


@pytest.mark.subprocess
def test_direct_integration_import_registers_web_subscribers() -> None:
    import sys

    import ddtrace.contrib  # noqa: F401
    from ddtrace.internal import core
    from ddtrace.internal.products import manager

    assert manager._products is None
    assert "ddtrace._trace.product" not in sys.modules
    assert core.has_listeners("context.started.web.request")
    assert core.has_listeners("context.ended.web.request")


@pytest.mark.parametrize("shutdown", ["stop_products", "exit_products"])
def test_manager_owns_declared_bundle_listeners(monkeypatch: pytest.MonkeyPatch, shutdown: str) -> None:
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
    product.subscriber_bundles = (SubscriberBundle(module.__name__, ("RequestSubscriber",)),)
    manager = ProductManager()
    manager._products = [("test", product)]
    context = core.context_with_event(RegistrationEvent())
    # stop() drains the active context while its completion listener is still registered.
    product.stop.side_effect = lambda join=False: context.__exit__(None, None, None)
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


@pytest.mark.subprocess
def test_tracing_product_declaration_does_not_import_subscribers() -> None:
    import sys

    from ddtrace._trace.product import WEB_FRAMEWORK_SUBSCRIBERS

    assert WEB_FRAMEWORK_SUBSCRIBERS.module == "ddtrace._trace.subscribers.web_framework"
    assert "ddtrace._trace.subscribers.web_framework" not in sys.modules
