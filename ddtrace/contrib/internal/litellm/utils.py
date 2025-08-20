from collections import defaultdict

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import LITELLM_ROUTER_INSTANCE_KEY
from ddtrace.llmobs._integrations.base_stream_handler import AsyncStreamHandler
from ddtrace.llmobs._integrations.base_stream_handler import StreamHandler
from ddtrace.llmobs._integrations.utils import openai_construct_completion_from_streamed_chunks
from ddtrace.llmobs._integrations.utils import openai_construct_message_from_streamed_chunks


log = get_logger(__name__)


def extract_host_tag(kwargs):
    if "host" in kwargs.get("metadata", {}).get("headers", {}):
        return kwargs["metadata"]["headers"]["host"]
    return None


class BaseLiteLLMStreamHandler:
    def initialize_chunk_storage(self):
        return defaultdict(list)

    def add_span(self, span, kwargs, instance):
        kwargs[LITELLM_ROUTER_INSTANCE_KEY] = instance
        self.spans.append((span, kwargs))

    def _process_chunk(self, chunk, iterator=None):
        for choice in getattr(chunk, "choices", []):
            choice_index = getattr(choice, "index", 0)
            self.chunks[choice_index].append(choice)
        if getattr(chunk, "usage", None):
            self.chunks[0].insert(0, chunk)

    def finalize_stream(self, exception=None):
        formatted_completions = None
        for span, kwargs in self.spans:
            if not formatted_completions:
                formatted_completions = self._process_finished_stream(span)
            if self.integration.is_pc_sampled_llmobs(span):
                self.integration.llmobs_set_tags(
                    span, args=[], kwargs=kwargs, response=formatted_completions, operation=span.resource
                )
            span.finish()

    def _process_finished_stream(self, span):
        try:
            operation = span.resource
            formatted_completions = None
            if self.integration.is_completion_operation(operation):
                formatted_completions = [
                    openai_construct_completion_from_streamed_chunks(choice) for choice in self.chunks.values()
                ]
            else:
                formatted_completions = [
                    openai_construct_message_from_streamed_chunks(choice) for choice in self.chunks.values()
                ]
            return formatted_completions
        except Exception:
            log.warning("Error processing streamed completion/chat response.", exc_info=True)
            return formatted_completions


class LiteLLMStreamHandler(BaseLiteLLMStreamHandler, StreamHandler):
    def process_chunk(self, chunk, iterator=None):
        self._process_chunk(chunk, iterator)


class LiteLLMAsyncStreamHandler(BaseLiteLLMStreamHandler, AsyncStreamHandler):
    async def process_chunk(self, chunk, iterator=None):
        self._process_chunk(chunk, iterator)
