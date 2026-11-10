from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from importlib import import_module
import pickle
import sys
from threading import Barrier
from threading import Event as ThreadEvent
from types import ModuleType
from types import TracebackType
from typing import Optional
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from ddtrace.internal import core
from ddtrace.internal.core import event_hub
from ddtrace.internal.core.events import Event
from ddtrace.internal.core.subscriber import ContextSubscriber
from ddtrace.internal.core.subscriber import Subscriber
from ddtrace.internal.core.subscriber import SubscriberBundle
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


@pytest.fixture
def request_bundle(monkeypatch):
    calls = []

    class RequestSubscriber(Subscriber):
        auto_register = False
        event_names = (RegistrationEvent.event_name,)

        @classmethod
        def on_event(cls, event: RegistrationEvent) -> None:
            calls.append(event)

    module = ModuleType("example.cached_setup")
    setattr(module, "RequestSubscriber", RequestSubscriber)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    bundle = SubscriberBundle(module.__name__, ("RequestSubscriber",))
    try:
        yield bundle, RequestSubscriber, calls
    finally:
        RequestSubscriber.unregister()


def test_active_bundle_skips_resolution_and_listener_lookup(request_bundle) -> None:
    bundle, subscriber, calls = request_bundle
    with patch("ddtrace.internal.core.subscriber.import_module", wraps=import_module) as load:
        with patch.object(core, "on", wraps=core.on) as on:
            bundle.register()
            bundle.register()
            bundle.register()
    load.assert_called_once_with(bundle.module)
    on.assert_called_once()
    event = RegistrationEvent()
    core.dispatch_event(event)
    assert calls == [event]


@pytest.mark.parametrize("removal", ["callback", "event", "hub", "subscriber", "bundle", "unrelated"])
def test_bundle_reactivates_after_listener_removal(request_bundle, removal) -> None:
    bundle, subscriber, calls = request_bundle
    with patch.object(subscriber, "register", wraps=subscriber.register) as register:
        bundle.ensure_registered()
        if removal == "callback":
            core.reset_listeners(RegistrationEvent.event_name, subscriber._on_event)
        elif removal == "event":
            core.reset_listeners(RegistrationEvent.event_name)
        elif removal == "hub":
            event_hub.reset(RegistrationEvent.event_name)
        elif removal == "subscriber":
            subscriber.unregister()
        elif removal == "bundle":
            bundle.unregister()
        else:
            core.reset_listeners("unrelated.event")
        # Cached resolution survives invalidation; only the listeners are restored.
        with patch("ddtrace.internal.core.subscriber.import_module") as load:
            bundle.ensure_registered()
            bundle.ensure_registered()
        load.assert_not_called()
        assert register.call_count == 2
    event = RegistrationEvent()
    core.dispatch_event(event)
    assert calls == [event]


def test_failed_bundle_activation_is_retried_without_duplicate_listeners(request_bundle, monkeypatch) -> None:
    bundle, subscriber, calls = request_bundle
    failing = Mock()
    failing.register.side_effect = [RuntimeError("registration failed"), None]
    monkeypatch.setattr(sys.modules[bundle.module], "FailingSubscriber", failing, raising=False)
    bundle = SubscriberBundle(bundle.module, ("RequestSubscriber", "FailingSubscriber"))
    with pytest.raises(RuntimeError, match="registration failed"):
        bundle.ensure_registered()
    bundle.ensure_registered()
    bundle.ensure_registered()
    assert failing.register.call_count == 2
    event = RegistrationEvent()
    core.dispatch_event(event)
    assert calls == [event]


def test_concurrent_bundle_activation_registers_once(request_bundle) -> None:
    bundle, subscriber, calls = request_bundle
    importing = Barrier(2)

    def load(name):
        importing.wait(timeout=5)
        return sys.modules[name]

    # Both threads must reach import outside the hub lock, then serialize resolution
    # and listener registration. This also detects holding the lock across imports.
    with patch("ddtrace.internal.core.subscriber.import_module", side_effect=load):
        with patch.object(subscriber, "register", wraps=subscriber.register) as register:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(bundle.ensure_registered) for _ in range(2)]
                for future in futures:
                    future.result(timeout=10)
            register.assert_called_once_with()
    event = RegistrationEvent()
    core.dispatch_event(event)
    assert calls == [event]


def test_reset_serializes_with_bundle_activation(request_bundle) -> None:
    bundle, subscriber, calls = request_bundle
    registering = ThreadEvent()
    removing = ThreadEvent()
    release = ThreadEvent()
    original_register = subscriber.register

    def register():
        original_register()
        registering.set()
        assert release.wait(timeout=5)

    def remove():
        removing.set()
        core.reset_listeners(RegistrationEvent.event_name)

    with patch.object(subscriber, "register", side_effect=register):
        with ThreadPoolExecutor(max_workers=2) as pool:
            activation = pool.submit(bundle.ensure_registered)
            try:
                assert registering.wait(timeout=5)
                removal = pool.submit(remove)
                assert removing.wait(timeout=5)
            finally:
                release.set()
            activation.result(timeout=10)
            removal.result(timeout=10)
    assert not core.has_listeners(RegistrationEvent.event_name)
    bundle.ensure_registered()
    event = RegistrationEvent()
    core.dispatch_event(event)
    assert calls == [event]


def test_reentrant_reset_does_not_cache_incomplete_activation(request_bundle) -> None:
    bundle, subscriber, calls = request_bundle
    original_register = subscriber.register

    def register_then_reset():
        original_register()
        core.reset_listeners(RegistrationEvent.event_name)

    with patch.object(subscriber, "register", side_effect=register_then_reset):
        bundle.ensure_registered()
    assert not core.has_listeners(RegistrationEvent.event_name)
    bundle.ensure_registered()
    event = RegistrationEvent()
    core.dispatch_event(event)
    assert calls == [event]


@pytest.mark.subprocess
def test_bundle_reactivates_after_full_hub_reset() -> None:
    from ddtrace._trace._registration import WEB_FRAMEWORK_SUBSCRIBERS
    from ddtrace.internal import core
    from ddtrace.internal.core import event_hub

    WEB_FRAMEWORK_SUBSCRIBERS.ensure_registered()
    event_hub.reset()
    assert not core.has_listeners("context.started.web.request")
    WEB_FRAMEWORK_SUBSCRIBERS.ensure_registered()
    assert core.has_listeners("context.started.web.request")
    assert core.has_listeners("context.ended.web.request")


@pytest.mark.subprocess()
def test_bundle_registration_lock_is_reset_after_fork() -> None:
    import os
    import signal
    from threading import Event
    from threading import Thread

    from ddtrace._trace._registration import WEB_FRAMEWORK_SUBSCRIBERS
    from ddtrace.internal import core
    from ddtrace.internal.core import event_hub

    WEB_FRAMEWORK_SUBSCRIBERS.ensure_registered()
    locked = Event()
    release = Event()

    def hold_lock():
        with event_hub._registration_lock:
            locked.set()
            assert release.wait(timeout=15)

    thread = Thread(target=hold_lock)
    thread.start()
    try:
        assert locked.wait(timeout=5)
        pid = os.fork()
        if pid == 0:
            signal.alarm(5)
            core.reset_listeners("context.started.web.request")
            WEB_FRAMEWORK_SUBSCRIBERS.ensure_registered()
            os._exit(0 if core.has_listeners("context.started.web.request") else 1)
        _, status = os.waitpid(pid, 0)
        assert status == 0
    finally:
        release.set()
        thread.join(timeout=5)


def test_serialized_bundle_does_not_preserve_process_local_activation(request_bundle) -> None:
    bundle, subscriber, calls = request_bundle
    with patch.object(subscriber, "register", wraps=subscriber.register) as register:
        bundle.ensure_registered()
        restored = pickle.loads(pickle.dumps(bundle))
        assert restored == bundle
        restored.ensure_registered()
        restored.ensure_registered()
        assert register.call_count == 2
    event = RegistrationEvent()
    core.dispatch_event(event)
    assert calls == [event]


@pytest.mark.subprocess()
def test_events_api_benchmark_scenarios_smoke() -> None:
    import sys
    from unittest.mock import patch

    sys.path.insert(0, "benchmarks")
    # Exercise the workloads without starting pyperf's worker processes.
    with patch("bm._scenario._register"):
        from benchmarks.events_api.scenario import EventsAPIScenario

    for api in (
        "core",
        "events",
        "dispatch",
        "dispatch_event",
        "trace_api",
        "subscriber_setup",
        "subscriber_first_activation",
        "subscriber_reset_recovery",
    ):
        scenario = EventsAPIScenario(name=api, api=api)
        workload = scenario.run()
        next(workload)(2)
        assert next(workload, None) is None
