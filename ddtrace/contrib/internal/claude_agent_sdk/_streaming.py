from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations.base_stream_handler import AsyncStreamHandler
from ddtrace.llmobs._integrations.base_stream_handler import make_traced_stream


log = get_logger(__name__)


def handle_streamed_response(integration, resp, args, kwargs, span, operation):
    return make_traced_stream(
        resp,
        ClaudeAgentSdkAsyncStreamHandler(integration, span, args, kwargs, operation=operation),
    )


class ClaudeAgentSdkAsyncStreamHandler(AsyncStreamHandler):
    def __init__(self, integration, span, args, kwargs, operation):
        super().__init__(integration, span, args, kwargs)
        self.operation = operation

    async def process_chunk(self, chunk, iterator=None):
        self.chunks.append(chunk)

    def finalize_stream(self, exception=None):
        try:
            self.integration.llmobs_set_tags(
                self.primary_span,
                args=self.request_args,
                kwargs=self.request_kwargs,
                response=self.chunks if self.chunks else None,
                operation=self.operation,
            )
        except Exception:
            log.warning("Error processing claude_agent_sdk stream response.", exc_info=True)
        finally:
            self.primary_span.finish()
