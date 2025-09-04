from collections import defaultdict
from typing import AsyncGenerator
from typing import Generator

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations.base_stream_handler import AsyncStreamHandler
from ddtrace.llmobs._integrations.base_stream_handler import StreamHandler
from ddtrace.llmobs._integrations.utils import openai_construct_completion_from_streamed_chunks
from ddtrace.llmobs._integrations.utils import openai_construct_message_from_streamed_chunks


log = get_logger(__name__)


class BaseOpenAIStreamHandler:
    def initialize_chunk_storage(self):
        return defaultdict(list)

    def finalize_stream(self, exception=None):
        if not exception:
            _process_finished_stream(
                self.integration,
                self.primary_span,
                self.request_kwargs,
                self.chunks,
                self.options.get("operation_type", ""),
            )
        self.primary_span.finish()


class OpenAIStreamHandler(BaseOpenAIStreamHandler, StreamHandler):
    def process_chunk(self, chunk, iterator=None):
        self._extract_token_chunk(chunk, iterator)
        _loop_handler(self.primary_span, chunk, self.chunks)

    def _extract_token_chunk(self, chunk, iterator=None):
        """Attempt to extract the token chunk (last chunk in the stream) from the streamed response."""
        if not self.primary_span._get_ctx_item("_dd.auto_extract_token_chunk"):
            return
        choices = getattr(chunk, "choices")
        if not choices:
            return
        choice = choices[0]
        if not getattr(choice, "finish_reason", None):
            # Only the second-last chunk in the stream with token usage enabled will have finish_reason set
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
        choices = getattr(chunk, "choices")
        if not choices:
            return
        choice = choices[0]
        if not getattr(choice, "finish_reason", None):
            return
        try:
            usage_chunk = await iterator.__anext__()
            self.chunks[0].insert(0, usage_chunk)
        except (StopAsyncIteration, GeneratorExit):
            return


def _format_openai_api_key(openai_api_key):
    # type: (Optional[str]) -> Optional[str]
    """
    Returns `sk-...XXXX`, where XXXX is the last 4 characters of the provided OpenAI API key.
    This mimics how OpenAI UI formats the API key.
    """
    if not openai_api_key:
        return None
    return "sk-...%s" % openai_api_key[-4:]


def _is_generator(resp):
    # type: (...) -> bool
    import openai

    # In OpenAI v1, the response is type `openai.Stream` instead of Generator.
    if isinstance(resp, Generator):
        return True
    if hasattr(openai, "Stream") and isinstance(resp, openai.Stream):
        return True
    return False


def _is_async_generator(resp):
    # type: (...) -> bool
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
        span.set_tag_str("openai.response.model", model)

    response = getattr(chunk, "response", None)
    if response is not None:
        streamed_chunks[0].insert(0, response)

    # Completions/chat completions are returned as `choices`
    for choice in getattr(chunk, "choices", []):
        streamed_chunks[choice.index].append(choice)
    if getattr(chunk, "usage", None):
        streamed_chunks[0].insert(0, chunk)


def _process_finished_stream(integration, span, kwargs, streamed_chunks, operation_type=""):
    if isinstance(streamed_chunks, dict) and operation_type != "response":
        streamed_chunks = streamed_chunks.values()
    try:
        if operation_type == "response":
            formatted_completions = streamed_chunks[0][0] if streamed_chunks and streamed_chunks[0] else None
        elif operation_type == "completion":
            formatted_completions = [
                openai_construct_completion_from_streamed_chunks(choice) for choice in streamed_chunks
            ]
        elif operation_type == "chat":
            formatted_completions = [
                openai_construct_message_from_streamed_chunks(choice) for choice in streamed_chunks
            ]
        integration.llmobs_set_tags(
            span, args=[], kwargs=kwargs, response=formatted_completions, operation=operation_type
        )
    except Exception:
        log.warning("Error processing streamed completion/chat response.", exc_info=True)
