import sys

import wrapt

from ddtrace.internal.logger import get_logger
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
        n = kwargs.get("n", 1) or 1
        self._dd_integration = integration
        self._dd_spans = [span]
        self._kwargs = [kwargs] # each span is associated with a different kwargs
        self._streamed_chunks = [[] for _ in range(n)]

    def add_router_span_info(self, span, kwargs, instance):
        """Handler to add router span to this streaming object.

        Helps to ensure that all spans associated with a single stream are finished and have the correct tags.
        """
        self._dd_spans.append(span)
        kwargs["router_instance"] = instance
        self._kwargs.append(kwargs)
    
    def finish_spans(self):
        """Helper to finish all spans associated with this stream."""
        formatted_completions = None
        for span, kwargs in zip(self._dd_spans, self._kwargs):
            if not formatted_completions:
                formatted_completions = _process_finished_stream(
                    self._dd_integration, span, kwargs, self._streamed_chunks, span.resource
                )
            elif self._dd_integration.is_pc_sampled_llmobs(span):
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
            for span in self._dd_spans:
                span.set_exc_info(*sys.exc_info())
            raise
        finally:
            self.finish_spans()

    def __next__(self):
        try:
            chunk = self.__wrapped__.__next__()
            _loop_handler(chunk, self._streamed_chunks)
            return chunk
        except StopIteration:
            raise
        except Exception:
            for span in self._dd_spans:
                span.set_exc_info(*sys.exc_info())
            raise
        finally:
            self.finish_spans()


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
            for span in self._dd_spans:
                span.set_exc_info(*sys.exc_info())
            raise
        finally:
            self.finish_spans()

    async def __anext__(self):
        try:
            chunk = await self.__wrapped__.__anext__()
            _loop_handler(chunk, self._streamed_chunks)
            return chunk
        except StopAsyncIteration:
            raise
        except Exception:
            for span in self._dd_spans:
                span.set_exc_info(*sys.exc_info())
            raise
        finally:
            self.finish_spans()


def _loop_handler(chunk, streamed_chunks):
    """Appends the chunk to the correct index in the streamed_chunks list.

    When handling a streamed chat/completion response, this function is called for each chunk in the streamed response.
    """
    for choice in chunk.choices:
        streamed_chunks[choice.index].append(choice)
    if getattr(chunk, "usage", None):
        streamed_chunks[0].insert(0, chunk)


def _process_finished_stream(integration, span, kwargs, streamed_chunks, operation):
    try:
        formatted_completions = None
        is_completion = "text" in operation
        if is_completion:
            formatted_completions = [
                openai_construct_completion_from_streamed_chunks(choice) for choice in streamed_chunks
            ]
        else:
            formatted_completions = [
                openai_construct_message_from_streamed_chunks(choice) for choice in streamed_chunks
            ]
        operation = operation if "router" in operation else "completion" if is_completion else "chat"
        if integration.is_pc_sampled_llmobs(span):
            integration.llmobs_set_tags(
                span, args=[], kwargs=kwargs, response=formatted_completions, operation=operation
            )
    except Exception:
        log.warning("Error processing streamed completion/chat response.", exc_info=True)
    finally:
        return formatted_completions
