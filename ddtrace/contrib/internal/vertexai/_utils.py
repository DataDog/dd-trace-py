from ddtrace.llmobs._integrations.base_stream_handler import AsyncStreamHandler
from ddtrace.llmobs._integrations.base_stream_handler import StreamHandler


class BaseVertexAIStreamHandler:
    def _process_chunk(self, chunk):
        # only keep track of the first chunk for chat messages since
        # it is modified during the streaming process
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
