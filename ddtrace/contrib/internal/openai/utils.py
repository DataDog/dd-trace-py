from collections import defaultdict
from typing import Any
from typing import AsyncGenerator
from typing import Generator
from typing import Optional

from ddtrace import config
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.contrib.internal.openai._constants import BASE_URL
from ddtrace.contrib.internal.openai._constants import COMPONENT
from ddtrace.contrib.internal.openai._constants import SUBMITTED_OPERATION_IDS
from ddtrace.contrib.internal.openai._constants import provider_name
from ddtrace.contrib.internal.stream_handler import AsyncStreamHandler
from ddtrace.contrib.internal.stream_handler import StreamHandler
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT as COMPONENT_TAG
from ddtrace.internal.logger import get_logger
from ddtrace.internal.span_bus import span_from_context
from ddtrace.internal.utils.openai import openai_construct_completion_from_streamed_chunks
from ddtrace.internal.utils.openai import openai_construct_message_from_streamed_chunks


log = get_logger(__name__)

# The most recently initialized client, whose base URL is used for requests that do not carry their own.
_default_client: Any = None


def set_default_client(client: Any) -> None:
    global _default_client
    _default_client = client


def _base_url(instance: Any) -> Optional[str]:
    client = getattr(instance, "_client", None) or _default_client
    base_url = getattr(client, "_base_url", None)
    return str(base_url) if base_url else None


def start_request(
    operation_id: str,
    instance: Any,
    kwargs: dict[str, Any],
    operation: str = "",
    submit_to_llmobs: Optional[bool] = None,
) -> core.ExecutionContext[LlmRequestEvent]:
    """Start the request's span, which stays active until the returned context's ended event is dispatched.

    operation is the kind of LLM request (completion, chat, embedding, response, decision or tool) that products
    tag when the span finishes, or empty for the OpenAI requests that are only traced.
    """
    event = LlmRequestEvent(
        component=COMPONENT,
        integration_config=config.openai,
        service=int_service(None, config.openai),
        resource=operation_id,
        provider=None,
        submit_to_llmobs=operation_id in SUBMITTED_OPERATION_IDS if submit_to_llmobs is None else submit_to_llmobs,
        request_kwargs=kwargs,
        # Products read the client's endpoint from the instance to detect proxied requests.
        instance=instance,
        operation=operation,
    )
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        span = span_from_context(ctx)
        base_url = _base_url(instance)
        if base_url:
            span._set_ctx_item(BASE_URL, base_url)
        # The LLM request subscriber drops the component tag when the span starts, so set it once started.
        span._set_attribute(COMPONENT_TAG, config.openai.integration_name)
        span._set_attribute("openai.request.provider", provider_name(base_url or ""))
    return ctx


def finish_request(ctx: core.ExecutionContext[LlmRequestEvent], response: Any = None) -> None:
    """Finish the request's span; products tag it with response first."""
    ctx.event.response = response
    ctx.dispatch_ended_event()


class BaseOpenAIStreamHandler:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._finished_choices: set[int] = set()

    def initialize_chunk_storage(self):
        return defaultdict(list)

    def _all_choices_finished(self, chunk: Any) -> bool:
        """Return whether this chunk finishes the last of the n requested choices.

        With n > 1 the choices finish independently, so the chunk after the first finish_reason
        can still carry content for another choice.
        """
        finished = [c for c in getattr(chunk, "choices", None) or [] if getattr(c, "finish_reason", None)]
        if not finished:
            return False
        self._finished_choices.update(getattr(c, "index", 0) for c in finished)
        n = self.request_kwargs.get("n")
        return len(self._finished_choices) >= (n if isinstance(n, int) and n > 1 else 1)

    def finalize_stream(self, exception=None):
        operation_type = self.options.get("operation_type", "")
        finish_request(self.options["ctx"], _format_finished_stream(self.chunks, operation_type))


class OpenAIStreamHandler(BaseOpenAIStreamHandler, StreamHandler):
    def process_chunk(self, chunk, iterator=None):
        self._extract_token_chunk(chunk, iterator)
        _loop_handler(self.primary_span, chunk, self.chunks)

    def _extract_token_chunk(self, chunk, iterator=None):
        """Attempt to extract the token chunk (last chunk in the stream) from the streamed response."""
        if not self.primary_span._get_ctx_item("_dd.auto_extract_token_chunk"):
            return
        if not self._all_choices_finished(chunk):
            # The usage chunk follows the chunk that finishes the last choice
            return
        try:
            # User isn't expecting last token chunk to be present since it's not part of the default streamed response,
            # so we consume it and extract the token usage metadata before it reaches the user.
            usage_chunk = iterator.__next__()
            self.chunks[0].insert(0, usage_chunk)
        except (StopIteration, GeneratorExit):
            return


class OpenAIAsyncStreamHandler(BaseOpenAIStreamHandler, AsyncStreamHandler):
    async def process_chunk(self, chunk, iterator=None):
        await self._extract_token_chunk(chunk, iterator)
        _loop_handler(self.primary_span, chunk, self.chunks)

    async def _extract_token_chunk(self, chunk, iterator=None):
        """Attempt to extract the token chunk (last chunk in the stream) from the streamed response."""
        if not self.primary_span._get_ctx_item("_dd.auto_extract_token_chunk"):
            return
        if not self._all_choices_finished(chunk):
            return
        try:
            usage_chunk = await iterator.__anext__()
            self.chunks[0].insert(0, usage_chunk)
        except (StopAsyncIteration, GeneratorExit):
            return


def _format_openai_api_key(openai_api_key: Optional[str]) -> Optional[str]:
    """
    Returns `sk-...XXXX`, where XXXX is the last 4 characters of the provided OpenAI API key.
    This mimics how OpenAI UI formats the API key.
    """
    if not openai_api_key:
        return None
    return "sk-...%s" % openai_api_key[-4:]


def _is_generator(resp: object) -> bool:
    import openai

    # In OpenAI v1, the response is type `openai.Stream` instead of Generator.
    if isinstance(resp, Generator):
        return True
    if hasattr(openai, "Stream") and isinstance(resp, openai.Stream):
        return True
    return False


def _is_async_generator(resp: object) -> bool:
    import openai

    # In OpenAI v1, the response is type `openai.AsyncStream` instead of AsyncGenerator.
    if isinstance(resp, AsyncGenerator):
        return True
    if hasattr(openai, "AsyncStream") and isinstance(resp, openai.AsyncStream):
        return True
    return False


def _loop_handler(span, chunk, streamed_chunks):
    """
    Sets the openai model tag and appends the chunk to the correct index in the streamed_chunks list.
    When handling a streamed chat/completion/responses,
    this function is called for each chunk in the streamed response.
    """
    if not span.get_tag("openai.response.model"):
        if hasattr(chunk, "type") and chunk.type.startswith("response."):
            response = getattr(chunk, "response", None)
            model = getattr(response, "model", "")
        else:
            model = getattr(chunk, "model", "")
        if model is not None:
            span._set_attribute("openai.response.model", model)

    response = getattr(chunk, "response", None)
    if response is not None:
        streamed_chunks[0].insert(0, response)

    # Completions/chat completions are returned as `choices`
    for choice in getattr(chunk, "choices", []):
        streamed_chunks[choice.index].append(choice)
    if getattr(chunk, "usage", None):
        # Keep only the most-recent usage chunk at the front of the list so constructor
        # functions read the final cumulative total
        if streamed_chunks[0] and getattr(streamed_chunks[0][0], "usage", None):
            streamed_chunks[0][0] = chunk
        else:
            streamed_chunks[0].insert(0, chunk)


def _format_finished_stream(streamed_chunks, operation_type=""):
    """The response a finished stream amounts to, which products tag the request's span with."""
    if isinstance(streamed_chunks, dict) and operation_type != "response":
        streamed_chunks = streamed_chunks.values()
    try:
        if operation_type == "response":
            return streamed_chunks[0][0] if streamed_chunks and streamed_chunks[0] else None
        elif operation_type == "completion":
            return [openai_construct_completion_from_streamed_chunks(choice) for choice in streamed_chunks]
        elif operation_type == "chat":
            return [openai_construct_message_from_streamed_chunks(choice) for choice in streamed_chunks]
    except Exception:
        log.warning("Error processing streamed completion/chat response.", exc_info=True)
    return None
