from collections.abc import Awaitable
from collections.abc import Callable
import sys
from typing import Any

from mistralai import client
from mistralai.client.chat import Chat
from mistralai.client.embeddings import Embeddings
from mistralai.client.models.chatcompletionresponse import ChatCompletionResponse
from mistralai.client.models.embeddingresponse import EmbeddingResponse

from ddtrace import config
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.contrib.internal.mistralai._utils import MistralAIAsyncStreamHandler
from ddtrace.contrib.internal.mistralai._utils import MistralAIStreamHandler
from ddtrace.contrib.internal.mistralai._utils import extract_provider
from ddtrace.contrib.internal.stream_handler import make_traced_stream
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.internal import core
from ddtrace.internal.span_bus import span_from_context


config._add("mistralai", {})  # type: ignore[no-untyped-call]

# LLMObs subscribes to LlmEvents for this component; see ddtrace/llmobs/_contrib/mistralai.
COMPONENT = "mistralai"

# APM tags owned by this integration. ddtrace.llmobs._integrations.mistralai no longer
# writes them, so they must stay in sync with the snapshot expectations here.
MODEL_TAG = "mistralai.request.model"
PROVIDER_TAG = "mistralai.request.provider"


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


def _request_event(
    func: Callable[..., Any],
    instance: Chat | Embeddings,
    kwargs: dict[str, Any],
    operation: str,
) -> LlmRequestEvent:
    """Build the LlmRequestEvent for a chat or embedding call.

    request_kwargs carries the server_url-enriched kwargs because the LLMObs side
    re-derives the provider from them at span finish.
    """
    enriched_kwargs = _kwargs_with_server_url(instance, kwargs)
    provider = extract_provider(enriched_kwargs)
    model = kwargs.get("model", "")
    return LlmRequestEvent(
        component=COMPONENT,
        integration_config=config.mistralai,
        service=int_service(None, config.mistralai),
        resource="%s.%s" % (instance.__class__.__name__, func.__name__),
        provider=provider,
        model=model,
        tags={PROVIDER_TAG: provider, MODEL_TAG: model},
        submit_to_llmobs=True,
        request_kwargs=enriched_kwargs,
        instance=instance,
        operation=operation,
    )


def traced_chat_generate(
    func: Callable[..., ChatCompletionResponse],
    instance: Chat,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> ChatCompletionResponse:
    event = _request_event(func, instance, kwargs, "llm")
    with core.context_with_event(event):
        resp = func(*args, **kwargs)
        event.response = resp
        return resp


async def traced_async_chat_generate(
    func: Callable[..., Awaitable[ChatCompletionResponse]],
    instance: Chat,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> ChatCompletionResponse:
    event = _request_event(func, instance, kwargs, "llm")
    with core.context_with_event(event):
        resp = await func(*args, **kwargs)
        event.response = resp
        return resp


def traced_generate_stream(
    func: Callable[..., Any],
    instance: Chat,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    event = _request_event(func, instance, kwargs, "llm")
    # dispatch_end_event=False defers the ended event until the stream handler calls
    # ctx.dispatch_ended_event() in finalize_stream(). Errors before the stream exists
    # must dispatch it manually so the span finishes with the error info.
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            resp = func(*args, **kwargs)
        except Exception:
            ctx.dispatch_ended_event(*sys.exc_info())
            raise
        handler = MistralAIStreamHandler(  # type: ignore[no-untyped-call]
            None, span_from_context(ctx), args, event.request_kwargs, ctx=ctx
        )
        return make_traced_stream(resp, handler)


async def traced_async_generate_stream(
    func: Callable[..., Awaitable[Any]],
    instance: Chat,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    event = _request_event(func, instance, kwargs, "llm")
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        try:
            resp = await func(*args, **kwargs)
        except Exception:
            ctx.dispatch_ended_event(*sys.exc_info())
            raise
        handler = MistralAIAsyncStreamHandler(  # type: ignore[no-untyped-call]
            None, span_from_context(ctx), args, event.request_kwargs, ctx=ctx
        )
        return make_traced_stream(resp, handler)


def traced_embed_generate(
    func: Callable[..., EmbeddingResponse],
    instance: Embeddings,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> EmbeddingResponse:
    event = _request_event(func, instance, kwargs, "embedding")
    with core.context_with_event(event):
        resp = func(*args, **kwargs)
        event.response = resp
        return resp


async def async_traced_embed_generate(
    func: Callable[..., Awaitable[EmbeddingResponse]],
    instance: Embeddings,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> EmbeddingResponse:
    event = _request_event(func, instance, kwargs, "embedding")
    with core.context_with_event(event):
        resp = await func(*args, **kwargs)
        event.response = resp
        return resp


def patch() -> None:
    if getattr(client, "_datadog_patch", False):
        return

    client._datadog_patch = True

    wrap("mistralai.client.chat", "Chat.complete", traced_chat_generate)
    wrap("mistralai.client.chat", "Chat.complete_async", traced_async_chat_generate)
    wrap("mistralai.client.chat", "Chat.stream", traced_generate_stream)
    wrap("mistralai.client.chat", "Chat.stream_async", traced_async_generate_stream)
    wrap("mistralai.client.embeddings", "Embeddings.create", traced_embed_generate)
    wrap("mistralai.client.embeddings", "Embeddings.create_async", async_traced_embed_generate)

    # Let products (LLMObs) attach their own LlmEvents subscribers without this
    # module importing them.
    core.dispatch("mistralai.patch", tuple())


def unpatch() -> None:
    if not getattr(client, "_datadog_patch", False):
        return

    client._datadog_patch = False

    core.dispatch("mistralai.unpatch", tuple())

    unwrap(client.chat.Chat, "complete")
    unwrap(client.chat.Chat, "complete_async")
    unwrap(client.chat.Chat, "stream")
    unwrap(client.chat.Chat, "stream_async")
    unwrap(client.embeddings.Embeddings, "create")
    unwrap(client.embeddings.Embeddings, "create_async")
