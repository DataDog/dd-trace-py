from ddtrace.llmobs._integrations.base_stream_handler import AsyncStreamHandler
from ddtrace.llmobs._integrations.base_stream_handler import StreamHandler


class BaseGoogleGenerativeAIStramHandler:
    def finalize_stream(self, exception=None):
        self.request_kwargs["instance"] = self.options.get("model_instance", None)
        self.integration.llmobs_set_tags(
            self.primary_span,
            args=self.request_args,
            kwargs=self.request_kwargs,
            response=self.options.get("wrapped_stream", None),
        )
        self.primary_span.finish()


class GoogleGenerativeAIStramHandler(BaseGoogleGenerativeAIStramHandler, StreamHandler):
    def process_chunk(self, chunk, iterator=None):
        pass


class GoogleGenerativeAIAsyncStreamHandler(BaseGoogleGenerativeAIStramHandler, AsyncStreamHandler):
    async def process_chunk(self, chunk, iterator=None):
        pass
