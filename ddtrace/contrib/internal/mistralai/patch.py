import mistralai.client as _mistralai_client
import mistralai.client.chat
import mistralai.client.embeddings

from ddtrace.llmobs._integrations import MistralAIIntegration
from ddtrace import config
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap


config._add("mistralai", {})


def _supported_versions():
    return {"mistralai": ">=2.0.0"}


def get_version() -> str:
    return getattr(_mistralai_client, "__version__", "")


def traced_chat_generate(func, instance, args, kwargs):
    integration = _mistralai_client._datadog_integration
    provider_name, model_name = "mistral", kwargs.get("model", "")

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
            integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=resp, operation="llm")


async def traced_async_chat_generate(func, instance, args, kwargs):
    integration = _mistralai_client._datadog_integration
    provider_name, model_name = "mistral", kwargs.get("model", "")

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
            integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=resp, operation="llm")


def traced_embed_generate(func, instance, args, kwargs):
    integration = _mistralai_client._datadog_integration
    provider_name, model_name = "mistral", kwargs.get("model", "")

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
            integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=resp, operation="embedding")


async def async_traced_embed_generate(func, instance, args, kwargs):
    integration = _mistralai_client._datadog_integration
    provider_name, model_name = "mistral", kwargs.get("model", "")

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
            integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=resp, operation="embedding")


def patch():
    if getattr(_mistralai_client, "_datadog_patch", False):
        return

    _mistralai_client._datadog_patch = True
    integration = MistralAIIntegration(integration_config=config.mistralai)
    _mistralai_client._datadog_integration = integration

    wrap("mistralai.client.chat", "Chat.complete", traced_chat_generate)
    wrap("mistralai.client.chat", "Chat.complete_async", traced_async_chat_generate)
    wrap("mistralai.client.embeddings", "Embeddings.create", traced_embed_generate)
    wrap("mistralai.client.embeddings", "Embeddings.create_async", async_traced_embed_generate)


def unpatch():
    if not getattr(_mistralai_client, "_datadog_patch", False):
        return

    _mistralai_client._datadog_patch = False

    unwrap(_mistralai_client.chat.Chat, "complete")
    unwrap(_mistralai_client.chat.Chat, "complete_async")
    unwrap(_mistralai_client.embeddings.Embeddings, "create")
    unwrap(_mistralai_client.embeddings.Embeddings, "create_async")

    delattr(_mistralai_client, "_datadog_integration")
