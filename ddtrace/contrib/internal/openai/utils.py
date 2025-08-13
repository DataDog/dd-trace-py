import sys
from typing import AsyncGenerator
from typing import Generator

import wrapt

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations.utils import openai_construct_completion_from_streamed_chunks
from ddtrace.llmobs._integrations.utils import openai_construct_message_from_streamed_chunks


log = get_logger(__name__)


class BaseTracedOpenAIStream(wrapt.ObjectProxy):
    def __init__(self, wrapped, integration, span, kwargs, operation_type="chat"):
        super().__init__(wrapped)
        n = kwargs.get("n", 1) or 1
        prompts = kwargs.get("prompt", "")
        if operation_type == "completion" and prompts and isinstance(prompts, list) and not isinstance(prompts[0], int):
            n *= len(prompts)
        self._dd_span = span
        self._streamed_chunks = [[] for _ in range(n)]
        self._dd_integration = integration
        self._operation_type = operation_type
        self._kwargs = kwargs


class TracedOpenAIStream(BaseTracedOpenAIStream):
    """
    This class is used to trace OpenAI stream objects for chat/completion/response.
    """

    def __enter__(self):
        self.__wrapped__.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.__wrapped__.__exit__(exc_type, exc_val, exc_tb)

    def __iter__(self):
        exception_raised = False
        try:
            for chunk in self.__wrapped__:
                self._extract_token_chunk(chunk)
                yield chunk
                _loop_handler(self._dd_span, chunk, self._streamed_chunks)
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            exception_raised = True
            raise
        finally:
            if not exception_raised:
                _process_finished_stream(
                    self._dd_integration,
                    self._dd_span,
                    self._kwargs,
                    self._streamed_chunks,
                    self._operation_type,
                )
            self._dd_span.finish()

    def __next__(self):
        try:
            chunk = self.__wrapped__.__next__()
            self._extract_token_chunk(chunk)
            _loop_handler(self._dd_span, chunk, self._streamed_chunks)
            return chunk
        except StopIteration:
            _process_finished_stream(
                self._dd_integration,
                self._dd_span,
                self._kwargs,
                self._streamed_chunks,
                self._operation_type,
            )
            self._dd_span.finish()
            raise
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            self._dd_span.finish()
            raise

    def _extract_token_chunk(self, chunk):
        """Attempt to extract the token chunk (last chunk in the stream) from the streamed response."""
        if not self._dd_span._get_ctx_item("_dd.auto_extract_token_chunk"):
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
            usage_chunk = self.__wrapped__.__next__()
            self._streamed_chunks[0].insert(0, usage_chunk)
        except (StopIteration, GeneratorExit):
            return


class TracedOpenAIAsyncStream(BaseTracedOpenAIStream):
    """
    This class is used to trace AsyncOpenAI stream objects for chat/completion/response.
    """

    async def __aenter__(self):
        await self.__wrapped__.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.__wrapped__.__aexit__(exc_type, exc_val, exc_tb)

    async def __aiter__(self):
        exception_raised = False
        try:
            async for chunk in self.__wrapped__:
                await self._extract_token_chunk(chunk)
                yield chunk
                _loop_handler(self._dd_span, chunk, self._streamed_chunks)
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            exception_raised = True
            raise
        finally:
            if not exception_raised:
                _process_finished_stream(
                    self._dd_integration, self._dd_span, self._kwargs, self._streamed_chunks, self._operation_type
                )
            self._dd_span.finish()

    async def __anext__(self):
        try:
            chunk = await self.__wrapped__.__anext__()
            await self._extract_token_chunk(chunk)
            _loop_handler(self._dd_span, chunk, self._streamed_chunks)
            return chunk
        except StopAsyncIteration:
            _process_finished_stream(
                self._dd_integration, self._dd_span, self._kwargs, self._streamed_chunks, self._operation_type
            )
            self._dd_span.finish()
            raise
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            self._dd_span.finish()
            raise

    async def _extract_token_chunk(self, chunk):
        """Attempt to extract the token chunk (last chunk in the stream) from the streamed response."""
        if not self._dd_span._get_ctx_item("_dd.auto_extract_token_chunk"):
            return
        choices = getattr(chunk, "choices")
        if not choices:
            return
        choice = choices[0]
        if not getattr(choice, "finish_reason", None):
            return
        try:
            usage_chunk = await self.__wrapped__.__anext__()
            self._streamed_chunks[0].insert(0, usage_chunk)
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
    if span.get_tag("openai.response.model") is None:
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
