import mistralai
import mistralai.client.chat
import mistralai.client.embeddings

from ddtrace.llmobs._integrations import MistralAIIntegration
from ddtrace import config
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap


config._add("mistralai", {})


def get_version() -> str:
    return getattr(mistralai, "__version__", "")


def traced_chat_generate(func, instance, args, kwargs):
    integration = mistralai._datadog_integration
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
    integration = mistralai._datadog_integration
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
    integration = mistralai._datadog_integration
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
    integration = mistralai._datadog_integration
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
    if getattr(mistralai, "_datadog_patch", False):
        return

    mistralai._datadog_patch = True
    integration = MistralAIIntegration(integration_config=config.mistralai)
    mistralai._datadog_integration = integration

    wrap("mistralai.client.chat", "Chat.complete", traced_chat_generate)
    wrap("mistralai.client.chat", "Chat.complete_async", traced_async_chat_generate)
    wrap("mistralai.client.embeddings", "Embeddings.create", traced_embed_generate)
    wrap("mistralai.client.embeddings", "Embeddings.create_async", async_traced_embed_generate)


def unpatch():
    if not getattr(mistralai, "_datadog_patch", False):
        return

    mistralai._datadog_patch = False

    unwrap(mistralai.client.chat.Chat, "complete")
    unwrap(mistralai.client.chat.Chat, "complete_async")
    unwrap(mistralai.client.embeddings.Embeddings, "create")
    unwrap(mistralai.client.embeddings.Embeddings, "create_async")

    delattr(mistralai, "_datadog_integration")
