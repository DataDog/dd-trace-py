from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any

from mistralai import client
from mistralai.client.chat import Chat
from mistralai.client.embeddings import Embeddings
from mistralai.client.models.chatcompletionresponse import ChatCompletionResponse
from mistralai.client.models.embeddingresponse import EmbeddingResponse

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.internal.schema import schematize_service_name
from ddtrace.llmobs._integrations import MistralAIIntegration
from ddtrace.llmobs._integrations.mistralai_utils import extract_provider


config._add(  # type: ignore[no-untyped-call]
    "mistralai",
    dict(_default_service=schematize_service_name("mistralai")),  # type: ignore[operator]
)


def _supported_versions() -> dict[str, str]:
    return {"mistralai": ">=2.0.0"}


def get_version() -> str:
    return getattr(client, "__version__", "")


def _kwargs_with_server_url(instance: Chat | Embeddings, kwargs: dict[str, Any]) -> dict[str, Any]:
    if "server_url" in kwargs:
        return kwargs
    sdk_configuration = getattr(instance, "sdk_configuration", None)
    if sdk_configuration is None:
        return kwargs
    instance_url = getattr(sdk_configuration, "server_url", None)
    if instance_url:
        kwargs = dict(kwargs, server_url=instance_url)
    return kwargs


def traced_chat_generate(
    func: Callable[..., ChatCompletionResponse],
    instance: Chat,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> ChatCompletionResponse:
    integration: MistralAIIntegration = client._datadog_integration
    enriched_kwargs = _kwargs_with_server_url(instance, kwargs)
    provider_name = extract_provider(enriched_kwargs)
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
            integration.llmobs_set_tags(span, args=list(args), kwargs=enriched_kwargs, response=resp, operation="llm")


async def traced_async_chat_generate(
    func: Callable[..., Awaitable[ChatCompletionResponse]],
    instance: Chat,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> ChatCompletionResponse:
    integration: MistralAIIntegration = client._datadog_integration
    enriched_kwargs = _kwargs_with_server_url(instance, kwargs)
    provider_name = extract_provider(enriched_kwargs)
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
            integration.llmobs_set_tags(span, args=list(args), kwargs=enriched_kwargs, response=resp, operation="llm")


def traced_embed_generate(
    func: Callable[..., EmbeddingResponse],
    instance: Embeddings,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> EmbeddingResponse:
    integration: MistralAIIntegration = client._datadog_integration
    enriched_kwargs = _kwargs_with_server_url(instance, kwargs)
    provider_name = extract_provider(enriched_kwargs)
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
            integration.llmobs_set_tags(
                span, args=list(args), kwargs=enriched_kwargs, response=resp, operation="embedding"
            )


async def async_traced_embed_generate(
    func: Callable[..., Awaitable[EmbeddingResponse]],
    instance: Embeddings,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> EmbeddingResponse:
    integration: MistralAIIntegration = client._datadog_integration
    enriched_kwargs = _kwargs_with_server_url(instance, kwargs)
    provider_name = extract_provider(enriched_kwargs)
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
            integration.llmobs_set_tags(
                span, args=list(args), kwargs=enriched_kwargs, response=resp, operation="embedding"
            )


def patch() -> None:
    if getattr(client, "_datadog_patch", False):
        return

    client._datadog_patch = True
    integration = MistralAIIntegration(integration_config=config.mistralai)
    client._datadog_integration = integration

    wrap("mistralai.client.chat", "Chat.complete", traced_chat_generate)
    wrap("mistralai.client.chat", "Chat.complete_async", traced_async_chat_generate)
    wrap("mistralai.client.embeddings", "Embeddings.create", traced_embed_generate)
    wrap("mistralai.client.embeddings", "Embeddings.create_async", async_traced_embed_generate)


def unpatch() -> None:
    if not getattr(client, "_datadog_patch", False):
        return

    client._datadog_patch = False

    unwrap(client.chat.Chat, "complete")
    unwrap(client.chat.Chat, "complete_async")
    unwrap(client.embeddings.Embeddings, "create")
    unwrap(client.embeddings.Embeddings, "create_async")

    delattr(client, "_datadog_integration")
