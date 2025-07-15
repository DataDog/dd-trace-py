import sys

from ddtrace.llmobs._integrations.base_stream_handler import AsyncStreamHandler
from ddtrace.llmobs._integrations.base_stream_handler import StreamHandler
from ddtrace.llmobs._utils import _get_attr

class BaseVertexAIStreamHandler:
    def initialize_chunk_storage(self):
        return []
    
    def _process_chunk(self, chunk):
        if not self.options.get("is_chat", False) or not self.chunks:
            self.chunks.append(chunk)

    def finalize_stream(self, exception=None):
        self.request_kwargs["instance"] = self.options.get("model_instance", None)
        self.request_kwargs["history"] = self.options.get("history", None)
        self.integration.llmobs_set_tags(
            self.primary_span, args=self.request_args, kwargs=self.request_kwargs, response=self.chunks
        )
        self.primary_span.finish()

class VertexAIStreamHandler(BaseVertexAIStreamHandler, StreamHandler):
    def process_chunk(self, chunk, iterator=None):
        self._process_chunk(chunk)

class VertexAIAsyncStreamHandler(BaseVertexAIStreamHandler, AsyncStreamHandler):
    async def process_chunk(self, chunk, iterator=None):
        self._process_chunk(chunk)

def extract_info_from_parts(parts):
    """Return concatenated text from parts and function calls."""
    concatenated_text = ""
    function_calls = []
    for part in parts:
        text = _get_attr(part, "text", "")
        concatenated_text += text
        function_call = _get_attr(part, "function_call", None)
        if function_call is not None:
            function_calls.append(function_call)
    return concatenated_text, function_calls
