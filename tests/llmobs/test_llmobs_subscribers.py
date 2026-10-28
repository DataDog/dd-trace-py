from unittest import mock

import pytest

from ddtrace import config
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.internal import core
from ddtrace.internal.products import manager
from ddtrace.llmobs import _product
from ddtrace.llmobs import _subscribers
from ddtrace.llmobs._subscribers import LLMObsLlmSubscriber
from ddtrace.llmobs._subscribers import integration_for


@pytest.fixture
def subscriber():
    LLMObsLlmSubscriber.unregister()
    yield LLMObsLlmSubscriber
    LLMObsLlmSubscriber.unregister()
    if "llmobs" in manager._registered:
        LLMObsLlmSubscriber.register()


@pytest.fixture
def integrations(monkeypatch):
    integrations = {}
    for component in ("anthropic", "llama_index"):
        integration = mock.Mock(llmobs_enabled=False)
        integration._is_instrumented_proxy_url.return_value = False
        integrations[component] = integration
    monkeypatch.setattr(_subscribers, "_integrations", dict(integrations))
    return integrations


def _request(component, **kwargs):
    event = LlmRequestEvent(
        component=component, integration_config=getattr(config, component), provider="test", **kwargs
    )
    with core.context_with_event(event):
        pass


@pytest.mark.parametrize("component", ["anthropic", "llama_index"])
def test_integration_for_infers_the_integration_from_the_component(component, monkeypatch):
    monkeypatch.setattr(_subscribers, "_integrations", {})

    integration = integration_for(component)

    assert type(integration)._integration_name == component
    assert integration_for(component) is integration


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


def test_patching_an_llmobs_integration_registers_llmobs_subscribers():
    with mock.patch.object(manager, "register_subscribers") as register_subscribers:
        _product.on_integration_patched("flask")
        register_subscribers.assert_not_called()

        _product.on_integration_patched("llama_index")
        register_subscribers.assert_called_once_with("llmobs")
