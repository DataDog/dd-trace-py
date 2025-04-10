import sys
from typing import Any
from typing import Dict
from typing import List

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations.utils import (
    openai_construct_completion_from_streamed_chunks,
    openai_construct_message_from_streamed_chunks,
)

log = get_logger(__name__)


class BaseTracedLiteLLMStream:
    def __init__(self, generator, integration, span, kwargs, is_completion=False):
        n = kwargs.get("n", 1) or 1
        self._generator = generator
        self._dd_integration = integration
        self._dd_span = span
        self._kwargs = kwargs
        self._streamed_chunks = [[] for _ in range(n)]
        self._is_completion = is_completion


class TracedLiteLLMStream(BaseTracedLiteLLMStream):
    def __enter__(self):
        self._generator.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._generator.__exit__(exc_type, exc_val, exc_tb)

    def __iter__(self):
        try:
            for chunk in self._generator:
                yield chunk
                _loop_handler(chunk, self._streamed_chunks)
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            raise
        finally:
            _process_finished_stream(
                self._dd_integration, self._dd_span, self._kwargs, self._streamed_chunks, self._is_completion
            )
            self._dd_span.finish()

    def __next__(self):
        try:
            chunk = self._generator.__next__()
            _loop_handler(chunk, self._streamed_chunks)
            return chunk
        except StopIteration:
            raise
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            raise
        finally:
            _process_finished_stream(
                self._dd_integration, self._dd_span, self._kwargs, self._streamed_chunks, self._is_completion
            )
            self._dd_span.finish()


class TracedLiteLLMAsyncStream(BaseTracedLiteLLMStream):
    async def __aenter__(self):
        await self._generator.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._generator.__aexit__(exc_type, exc_val, exc_tb)

    async def __aiter__(self):
        try:
            async for chunk in self._generator:
                yield chunk
                _loop_handler(chunk, self._streamed_chunks)
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            raise
        finally:
            _process_finished_stream(
                self._dd_integration, self._dd_span, self._kwargs, self._streamed_chunks, self._is_completion
            )
            self._dd_span.finish()

    async def __anext__(self):
        try:
            chunk = await self._generator.__anext__()
            _loop_handler(chunk, self._streamed_chunks)
            return chunk
        except StopAsyncIteration:
            raise
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            raise
        finally:
            _process_finished_stream(
                self._dd_integration, self._dd_span, self._kwargs, self._streamed_chunks, self._is_completion
            )
            self._dd_span.finish()


def _loop_handler(chunk, streamed_chunks):
    """Appends the chunk to the correct index in the streamed_chunks list.

    When handling a streamed chat/completion response, this function is called for each chunk in the streamed response.
    """
    for choice in chunk.choices:
        streamed_chunks[choice.index].append(choice)
    if getattr(chunk, "usage", None):
        streamed_chunks[0].insert(0, chunk)


def _process_finished_stream(integration, span, kwargs, streamed_chunks, is_completion=False):
    try:
        if is_completion:
            formatted_completions = [
                openai_construct_completion_from_streamed_chunks(choice) for choice in streamed_chunks
            ]
        else:
            formatted_completions = [
                openai_construct_message_from_streamed_chunks(choice) for choice in streamed_chunks
            ]
        operation = "completion" if is_completion else "chat"
        if integration.is_pc_sampled_llmobs(span):
            integration.llmobs_set_tags(
                span, args=[], kwargs=kwargs, response=formatted_completions, operation=operation
            )
    except Exception:
        log.warning("Error processing streamed completion/chat response.", exc_info=True)
