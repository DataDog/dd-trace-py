from collections import defaultdict
from dataclasses import FrozenInstanceError

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
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
        # Capture the first non-empty response model. Only emitted as a span tag for azure/azure_text
        # providers (in finalize_stream), where the request model is an arbitrary deployment name.
        if not getattr(self, "response_model", None):
            chunk_model = getattr(chunk, "model", None)
            if chunk_model:
                try:
                    self.response_model = chunk_model
                except (AttributeError, FrozenInstanceError):
                    pass
        for choice in getattr(chunk, "choices", []):
            choice_index = getattr(choice, "index", 0)
            self.chunks[choice_index].append(choice)
        if getattr(chunk, "usage", None):
            self.chunks[0].insert(0, chunk)

    def finalize_stream(self, exception=None):
        formatted_completions = None
        for span, kwargs in self.spans:
            response_model = getattr(self, "response_model", None)
            if response_model:
                # Resolve the requested model from args or kwargs (callers may pass it positionally,
                # e.g. litellm.completion("azure/<deployment>", ...)) so provider detection works.
                requested_model = get_argument_value(self.request_args, kwargs, 0, "model", optional=True) or ""
                _, provider = self.integration._model_map.get(requested_model, ("", ""))
                if provider in ("azure", "azure_text"):
                    span.set_tag("litellm.response.model", response_model)
            if not formatted_completions:
                formatted_completions = self._process_finished_stream(span)
            self.integration.llmobs_set_tags(
                span, args=self.request_args, kwargs=kwargs, response=formatted_completions, operation=span.resource
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
