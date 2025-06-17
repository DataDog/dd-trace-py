import sys

import wrapt

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import LITELLM_ROUTER_INSTANCE_KEY
from ddtrace.llmobs._integrations.utils import openai_construct_completion_from_streamed_chunks
from ddtrace.llmobs._integrations.utils import openai_construct_message_from_streamed_chunks
from ddtrace.llmobs._integrations.utils import LLMObsTracedStream, AsyncLLMObsTracedStream

log = get_logger(__name__)


def extract_host_tag(kwargs):
    if "host" in kwargs.get("metadata", {}).get("headers", {}):
        return kwargs["metadata"]["headers"]["host"]
    return None


class LiteLLMStreamMixin:
    def __init__(self, integration, span, kwargs):
        n = kwargs.get("n", 1) or 1
        self._dd_integration = integration
        self._span_info = [(span, kwargs)]
        self._streamed_chunks = [[] for _ in range(n)]
    
    def _add_router_span_info(self, span, kwargs, instance):
        """Handler to add router span to this streaming object.

        Helps to ensure that all spans associated with a single stream are finished and have the correct tags.
        """
        kwargs[LITELLM_ROUTER_INSTANCE_KEY] = instance
        self._span_info.append((span, kwargs))
    
    def chunk_callback(self, chunk):
        """Appends the chunk to the correct index in the streamed_chunks list.

        When handling a streamed chat/completion response, this function is called for each chunk in the streamed response.
        """
        for choice in chunk.choices:
            self._streamed_chunks[choice.index].append(choice)
        if getattr(chunk, "usage", None):
            self._streamed_chunks[0].insert(0, chunk)

    def exception_callback(self, e):
        if self._span_info and len(self._span_info[0]) > 0:
            span = self._span_info[0][0]
            span.set_exc_info(*sys.exc_info())

    def stream_finished_callback(self):
        """Helper to finish all spans associated with this stream."""
        formatted_completions = None
        for span, kwargs in self._span_info:
            if not formatted_completions:
                formatted_completions = _process_finished_stream(
                    self._dd_integration, span, kwargs, self._streamed_chunks, span.resource
                )
            elif self._dd_integration.is_pc_sampled_llmobs(span):
                self._dd_integration.llmobs_set_tags(
                    span, args=[], kwargs=kwargs, response=formatted_completions, operation=span.resource
                )
            span.finish()


class TracedLiteLLMStream(LiteLLMStreamMixin, LLMObsTracedStream):
    def __init__(self, wrapped, integration, span, kwargs):
        LLMObsTracedStream.__init__(self, wrapped)
        LiteLLMStreamMixin.__init__(self, integration, span, kwargs)


class TracedLiteLLMAsyncStream(LiteLLMStreamMixin, AsyncLLMObsTracedStream):
    def __init__(self, wrapped, integration, span, kwargs):
        AsyncLLMObsTracedStream.__init__(self, wrapped)
        LiteLLMStreamMixin.__init__(self, integration, span, kwargs)

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
        if integration.is_completion_operation(operation):
            formatted_completions = [
                openai_construct_completion_from_streamed_chunks(choice) for choice in streamed_chunks
            ]
        else:
            formatted_completions = [
                openai_construct_message_from_streamed_chunks(choice) for choice in streamed_chunks
            ]
        if integration.is_pc_sampled_llmobs(span):
            integration.llmobs_set_tags(
                span, args=[], kwargs=kwargs, response=formatted_completions, operation=operation
            )
    except Exception:
        log.warning("Error processing streamed completion/chat response.", exc_info=True)
    return formatted_completions
