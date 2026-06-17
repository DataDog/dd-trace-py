from mistralai import client

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.llmobs._integrations import MistralAIIntegration
from ddtrace.llmobs._integrations.mistralai import _extract_provider


config._add("mistralai", {})


def _supported_versions():
    return {"mistralai": ">=2.0.0"}


def get_version() -> str:
    return getattr(client, "__version__", "")


def _kwargs_with_server_url(instance, kwargs):
    if "server_url" in kwargs:
        return kwargs
    instance_url = getattr(getattr(instance, "sdk_configuration", None), "server_url", None)
    if instance_url:
        kwargs = dict(kwargs, server_url=instance_url)
    return kwargs


def traced_chat_generate(func, instance, args, kwargs):
    integration: MistralAIIntegration = client._datadog_integration
    enriched_kwargs = _kwargs_with_server_url(instance, kwargs)
    provider_name = _extract_provider(enriched_kwargs)
    model_name = kwargs.get("model", "")

    with integration.trace(
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        provider=provider_name,
        model=model_name,
        submit_to_llmobs=True,
    ) as span:
        resp = None
        try:
            resp = func(*args, **kwargs)
            return resp
        finally:
            integration.llmobs_set_tags(span, args=args, kwargs=enriched_kwargs, response=resp, operation="llm")


async def traced_async_chat_generate(func, instance, args, kwargs):
    integration: MistralAIIntegration = client._datadog_integration
    enriched_kwargs = _kwargs_with_server_url(instance, kwargs)
    provider_name = _extract_provider(enriched_kwargs)
    model_name = kwargs.get("model", "")

    with integration.trace(
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        provider=provider_name,
        model=model_name,
        submit_to_llmobs=True,
    ) as span:
        resp = None
        try:
            resp = await func(*args, **kwargs)
            return resp
        finally:
            integration.llmobs_set_tags(span, args=args, kwargs=enriched_kwargs, response=resp, operation="llm")


def traced_embed_generate(func, instance, args, kwargs):
    integration: MistralAIIntegration = client._datadog_integration
    enriched_kwargs = _kwargs_with_server_url(instance, kwargs)
    provider_name = _extract_provider(enriched_kwargs)
    model_name = kwargs.get("model", "")

    with integration.trace(
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        provider=provider_name,
        model=model_name,
        submit_to_llmobs=True,
    ) as span:
        resp = None
        try:
            resp = func(*args, **kwargs)
            return resp
        finally:
            integration.llmobs_set_tags(span, args=args, kwargs=enriched_kwargs, response=resp, operation="embedding")


async def async_traced_embed_generate(func, instance, args, kwargs):
    integration: MistralAIIntegration = client._datadog_integration
    enriched_kwargs = _kwargs_with_server_url(instance, kwargs)
    provider_name = _extract_provider(enriched_kwargs)
    model_name = kwargs.get("model", "")

    with integration.trace(
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        provider=provider_name,
        model=model_name,
        submit_to_llmobs=True,
    ) as span:
        resp = None
        try:
            resp = await func(*args, **kwargs)
            return resp
        finally:
            integration.llmobs_set_tags(span, args=args, kwargs=enriched_kwargs, response=resp, operation="embedding")


def patch():
    if getattr(client, "_datadog_patch", False):
        return

    client._datadog_patch = True
    integration = MistralAIIntegration(integration_config=config.mistralai)
    client._datadog_integration = integration

    wrap("mistralai.client.chat", "Chat.complete", traced_chat_generate)
    wrap("mistralai.client.chat", "Chat.complete_async", traced_async_chat_generate)
    wrap("mistralai.client.embeddings", "Embeddings.create", traced_embed_generate)
    wrap("mistralai.client.embeddings", "Embeddings.create_async", async_traced_embed_generate)


def unpatch():
    if not getattr(client, "_datadog_patch", False):
        return

    client._datadog_patch = False

    unwrap(client.chat.Chat, "complete")
    unwrap(client.chat.Chat, "complete_async")
    unwrap(client.embeddings.Embeddings, "create")
    unwrap(client.embeddings.Embeddings, "create_async")

    delattr(client, "_datadog_integration")
