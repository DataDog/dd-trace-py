from collections.abc import Awaitable
from collections.abc import Callable
import sys
from typing import Any
from typing import Union

from mistralai import client
from mistralai.client.chat import Chat
from mistralai.client.embeddings import Embeddings
from mistralai.client.models.chatcompletionresponse import ChatCompletionResponse
from mistralai.client.models.embeddingresponse import EmbeddingResponse

from ddtrace import config
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.contrib.internal.mistralai._constants import COMPONENT
from ddtrace.contrib.internal.mistralai._constants import TAG_MODEL
from ddtrace.contrib.internal.mistralai._constants import TAG_PROVIDER
from ddtrace.contrib.internal.mistralai._utils import MistralAIAsyncStreamHandler
from ddtrace.contrib.internal.mistralai._utils import MistralAIStreamHandler
from ddtrace.contrib.internal.mistralai._utils import extract_provider
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.internal import core
from ddtrace.internal import integration_hooks
from ddtrace.internal.span_bus import span_from_context
from ddtrace.internal.utils.streaming import make_traced_stream


config._add("mistralai", {})  # type: ignore[no-untyped-call]


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
    func: Callable[..., Any], instance: Union[Chat, Embeddings], kwargs: dict[str, Any], operation: str
) -> LlmRequestEvent:
    request_kwargs = _kwargs_with_server_url(instance, kwargs)
    provider = extract_provider(request_kwargs)
    model = kwargs.get("model", "")
    tags = {TAG_PROVIDER: provider}
    if model is not None:
        tags[TAG_MODEL] = model
    return LlmRequestEvent(
        component=COMPONENT,
        integration_config=config.mistralai,
        service=int_service(None, config.mistralai),
        resource="%s.%s" % (instance.__class__.__name__, func.__name__),
        provider=provider,
        model=model,
        tags=tags,
        submit_to_llmobs=True,
        request_kwargs=request_kwargs,
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
        event.response = func(*args, **kwargs)
        return event.response


async def traced_async_chat_generate(
    func: Callable[..., Awaitable[ChatCompletionResponse]],
    instance: Chat,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> ChatCompletionResponse:
    event = _request_event(func, instance, kwargs, "llm")
    with core.context_with_event(event):
        event.response = await func(*args, **kwargs)
        return event.response


def traced_generate_stream(
    func: Callable[..., Any],
    instance: Chat,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    event = _request_event(func, instance, kwargs, "llm")
    # The stream handler dispatches the ended event once the stream is consumed or closed.
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
        event.response = func(*args, **kwargs)
        return event.response


async def async_traced_embed_generate(
    func: Callable[..., Awaitable[EmbeddingResponse]],
    instance: Embeddings,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> EmbeddingResponse:
    event = _request_event(func, instance, kwargs, "embedding")
    with core.context_with_event(event):
        event.response = await func(*args, **kwargs)
        return event.response


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
    integration_hooks.on_patch("mistralai")


def unpatch() -> None:
    if not getattr(client, "_datadog_patch", False):
        return

    client._datadog_patch = False

    integration_hooks.on_unpatch("mistralai")

    unwrap(client.chat.Chat, "complete")
    unwrap(client.chat.Chat, "complete_async")
    unwrap(client.chat.Chat, "stream")
    unwrap(client.chat.Chat, "stream_async")
    unwrap(client.embeddings.Embeddings, "create")
    unwrap(client.embeddings.Embeddings, "create_async")
