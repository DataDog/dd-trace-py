from collections import defaultdict
import sys

import wrapt

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import LITELLM_ROUTER_INSTANCE_KEY
from ddtrace.llmobs._integrations.utils import openai_construct_completion_from_streamed_chunks
from ddtrace.llmobs._integrations.utils import openai_construct_message_from_streamed_chunks


log = get_logger(__name__)


def extract_host_tag(kwargs):
    if "host" in kwargs.get("metadata", {}).get("headers", {}):
        return kwargs["metadata"]["headers"]["host"]
    return None


class BaseTracedLiteLLMStream(wrapt.ObjectProxy):
    def __init__(self, wrapped, integration, span, kwargs):
        super().__init__(wrapped)
        self._dd_integration = integration
        self._span_info = [(span, kwargs)]
        self._streamed_chunks = defaultdict(list)

    def _add_router_span_info(self, span, kwargs, instance):
        """Handler to add router span to this streaming object.

        Helps to ensure that all spans associated with a single stream are finished and have the correct tags.
        """
        kwargs[LITELLM_ROUTER_INSTANCE_KEY] = instance
        self._span_info.append((span, kwargs))

    def _finish_spans(self):
        """Helper to finish all spans associated with this stream."""
        formatted_completions = None
        for span, kwargs in self._span_info:
            if not formatted_completions:
                formatted_completions = _process_finished_stream(
                    self._dd_integration, span, kwargs, self._streamed_chunks, span.resource
                )
            else:
                self._dd_integration.llmobs_set_tags(
                    span, args=[], kwargs=kwargs, response=formatted_completions, operation=span.resource
                )
            span.finish()


class TracedLiteLLMStream(BaseTracedLiteLLMStream):
    def __enter__(self):
        self.__wrapped__.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.__wrapped__.__exit__(exc_type, exc_val, exc_tb)

    def __iter__(self):
        try:
            for chunk in self.__wrapped__:
                yield chunk
                _loop_handler(chunk, self._streamed_chunks)
        except Exception:
            if self._span_info and len(self._span_info[0]) > 0:
                span = self._span_info[0][0]
                span.set_exc_info(*sys.exc_info())
            raise
        finally:
            self._finish_spans()

    def __next__(self):
        try:
            chunk = self.__wrapped__.__next__()
            _loop_handler(chunk, self._streamed_chunks)
            return chunk
        except StopIteration:
            raise
        except Exception:
            if self._span_info and len(self._span_info[0]) > 0:
                span = self._span_info[0][0]
                span.set_exc_info(*sys.exc_info())
            raise
        finally:
            self._finish_spans()


class TracedLiteLLMAsyncStream(BaseTracedLiteLLMStream):
    async def __aenter__(self):
        await self.__wrapped__.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.__wrapped__.__aexit__(exc_type, exc_val, exc_tb)

    async def __aiter__(self):
        try:
            async for chunk in self.__wrapped__:
                yield chunk
                _loop_handler(chunk, self._streamed_chunks)
        except Exception:
            if self._span_info and len(self._span_info[0]) > 0:
                span = self._span_info[0][0]
                span.set_exc_info(*sys.exc_info())
            raise
        finally:
            self._finish_spans()

    async def __anext__(self):
        try:
            chunk = await self.__wrapped__.__anext__()
            _loop_handler(chunk, self._streamed_chunks)
            return chunk
        except StopAsyncIteration:
            raise
        except Exception:
            if self._span_info and len(self._span_info[0]) > 0:
                span = self._span_info[0][0]
                span.set_exc_info(*sys.exc_info())
            raise
        finally:
            self._finish_spans()


def _loop_handler(chunk, streamed_chunks):
    """Appends the chunk to the correct index in the streamed_chunks list.

    When handling a streamed chat/completion response, this function is called for each chunk in the streamed response.
    """
    for choice in getattr(chunk, "choices", []):
        choice_index = getattr(choice, "index", 0)
        streamed_chunks[choice_index].append(choice)
    if getattr(chunk, "usage", None):
        streamed_chunks[0].insert(0, chunk)


def _process_finished_stream(integration, span, kwargs, streamed_chunks, operation):
    try:
        formatted_completions = None
        if integration.is_completion_operation(operation):
            formatted_completions = [
                openai_construct_completion_from_streamed_chunks(choice) for choice in streamed_chunks.values()
            ]
        else:
            formatted_completions = [
                openai_construct_message_from_streamed_chunks(choice) for choice in streamed_chunks.values()
            ]
        integration.llmobs_set_tags(span, args=[], kwargs=kwargs, response=formatted_completions, operation=operation)
    except Exception:
        log.warning("Error processing streamed completion/chat response.", exc_info=True)
    return formatted_completions
