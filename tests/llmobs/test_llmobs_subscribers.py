from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from threading import Event
from unittest import mock

import pytest

from ddtrace import config
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.internal import core
from ddtrace.llmobs import _product
from ddtrace.llmobs import _subscribers
from ddtrace.llmobs._registration import LLM_SUBSCRIBERS
from ddtrace.llmobs._subscribers import LLMObsLlmSubscriber
from ddtrace.llmobs._subscribers import integration_for


@pytest.fixture
def subscriber():
    was_registered = core.has_listeners("span.finishing.llm.request")
    LLMObsLlmSubscriber.unregister()
    yield LLMObsLlmSubscriber
    LLMObsLlmSubscriber.unregister()
    if was_registered:
        LLMObsLlmSubscriber.register()


@pytest.fixture
def integrations(monkeypatch):
    integrations = {}
    for component in ("anthropic", "llama_index"):
        integration = mock.Mock(llmobs_enabled=False)
        integration._is_instrumented_proxy_url.return_value = False
        integrations[component] = integration
    monkeypatch.setattr(_subscribers, "_integrations", dict(integrations))
    monkeypatch.setattr(_subscribers, "_integration_classes", {key: mock.Mock() for key in integrations})
    return integrations


def _request(component, **kwargs):
    event = LlmRequestEvent(
        component=component, integration_config=getattr(config, component), provider="test", **kwargs
    )
    with core.context_with_event(event):
        pass


def test_component_registration_is_lazy_and_cached(subscriber, monkeypatch):
    monkeypatch.setattr(_subscribers, "_integration_classes", {})
    monkeypatch.setattr(_subscribers, "_integrations", {})
    factory = mock.Mock()
    _subscribers.register("anthropic", factory)
    factory.assert_not_called()
    assert integration_for("anthropic") is factory.return_value
    assert integration_for("anthropic") is factory.return_value
    factory.assert_called_once_with(integration_config=config.anthropic)
    _subscribers.unregister("anthropic")
    assert integration_for("anthropic") is None


def test_integration_for_unknown_component(monkeypatch):
    monkeypatch.setattr(_subscribers, "_integrations", {})

    assert integration_for("not_an_integration") is None


def test_subscriber_tags_each_component_with_its_integration(subscriber, integrations):
    subscriber.register()

    _request("llama_index")
    integrations["llama_index"].llmobs_set_tags.assert_called_once()
    integrations["anthropic"].llmobs_set_tags.assert_not_called()

    _request("anthropic")
    integrations["anthropic"].llmobs_set_tags.assert_called_once()


def test_subscriber_finishes_with_the_integration_it_started_with(subscriber, integrations, monkeypatch):
    subscriber.register()
    first = integrations["llama_index"]
    replacement = mock.Mock(llmobs_enabled=False)

    event = LlmRequestEvent(component="llama_index", integration_config=config.llama_index, provider="test")
    with core.context_with_event(event):
        monkeypatch.setitem(_subscribers._integrations, "llama_index", replacement)

    first.llmobs_set_tags.assert_called_once()
    replacement.llmobs_set_tags.assert_not_called()


def test_subscriber_skips_events_carrying_their_own_integration(subscriber, integrations):
    subscriber.register()

    _request("llama_index", llmobs_integration=mock.Mock(llmobs_enabled=False))

    integrations["llama_index"].llmobs_set_tags.assert_not_called()


def test_subscriber_registers_once_and_unregisters(subscriber, integrations):
    subscriber.register()
    subscriber.register()
    _request("llama_index")
    integrations["llama_index"].llmobs_set_tags.assert_called_once()

    subscriber.unregister()
    _request("llama_index")
    integrations["llama_index"].llmobs_set_tags.assert_called_once()


def test_bundle_restores_listeners_after_reset(subscriber, integrations):
    LLM_SUBSCRIBERS.register()
    subscriber.unregister()
    LLM_SUBSCRIBERS.register()
    _request("llama_index")
    integrations["llama_index"].llmobs_set_tags.assert_called_once()


def test_product_stop_preserves_inflight_completion(subscriber, integrations):
    LLM_SUBSCRIBERS.register()
    event = LlmRequestEvent(component="llama_index", integration_config=config.llama_index, provider="test")
    with core.context_with_event(event):
        _product.stop()
    integrations["llama_index"].llmobs_set_tags.assert_called_once()


def test_compatibility_get_integration():
    from ddtrace.llmobs._contrib._subscribers import get_integration

    assert get_integration is integration_for


@pytest.mark.parametrize("cached", [True, False])
def test_integration_for_unregister_during_cache_lookup(monkeypatch, cached):
    integration = mock.Mock()

    class UnregisterOnLookup(dict):
        def get(self, component, default=None):
            result = super().get(component, default)
            _subscribers.unregister(component)
            return result

        def __contains__(self, component):
            result = super().__contains__(component)
            _subscribers.unregister(component)
            return result

    monkeypatch.setattr(_subscribers, "_integration_classes", {"anthropic": mock.Mock()})
    monkeypatch.setattr(
        _subscribers, "_integrations", UnregisterOnLookup(anthropic=integration) if cached else UnregisterOnLookup()
    )
    assert integration_for("anthropic") is (integration if cached else None)
    assert integration_for("anthropic") is None
    assert "anthropic" not in _subscribers._integrations


def test_unregister_during_creation_does_not_leave_cached_integration(subscriber, monkeypatch):
    monkeypatch.setattr(_subscribers, "_integration_classes", {})
    monkeypatch.setattr(_subscribers, "_integrations", {})
    constructing = Event()
    release_constructor = Event()
    unregistering = Event()

    def construct(**kwargs):
        constructing.set()
        assert release_constructor.wait(timeout=5)
        return mock.Mock()

    def unregister():
        unregistering.set()
        _subscribers.unregister("anthropic")

    factory = mock.Mock(side_effect=construct)
    _subscribers.register("anthropic", factory)
    with ThreadPoolExecutor(max_workers=2) as pool:
        lookup = pool.submit(integration_for, "anthropic")
        try:
            assert constructing.wait(timeout=5)
            removal = pool.submit(unregister)
            assert unregistering.wait(timeout=5)
            # Removal must wait for creation, then clear the newly created instance.
            with pytest.raises(FutureTimeoutError):
                removal.result(timeout=0.1)
        finally:
            release_constructor.set()
        first = lookup.result(timeout=5)
        removal.result(timeout=5)

    assert "anthropic" not in _subscribers._integrations
    assert integration_for("anthropic") is None
    _subscribers.register("anthropic", factory)
    replacement = integration_for("anthropic")
    assert replacement is not first
    assert integration_for("anthropic") is replacement
    assert factory.call_count == 2
