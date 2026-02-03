from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations.base_stream_handler import AsyncStreamHandler
from ddtrace.llmobs._integrations.base_stream_handler import make_traced_stream


log = get_logger(__name__)


def handle_streamed_response(integration, resp, args, kwargs, span, operation, instance=None):
    return make_traced_stream(
        resp,
        ClaudeAgentSdkAsyncStreamHandler(integration, span, args, kwargs, operation=operation, instance=instance),
    )


class ClaudeAgentSdkAsyncStreamHandler(AsyncStreamHandler):
    def __init__(self, integration, span, args, kwargs, operation, instance=None):
        super().__init__(integration, span, args, kwargs)
        self.operation = operation
        self.instance = instance
        self.context = None

    async def process_chunk(self, chunk, iterator=None):
        self.chunks.append(chunk)

        if type(chunk).__name__ == "ResultMessage" and self.instance and self.context is None:
            await self._retrieve_context()

    async def _retrieve_context(self):
        if self.instance is None:
            return
        try:
            # set flag to skip tracing during internal context retrieval
            self.instance._dd_internal_context_query = True
            await self.instance.query("/context")
            context_messages = []
            async for msg in self.instance.receive_response():
                context_messages.append(msg)
            self.context = context_messages
        except Exception:
            log.warning("Error retrieving after context from claude_agent_sdk", exc_info=True)
        finally:
            if self.instance is not None:
                self.instance._dd_internal_context_query = False

    def finalize_stream(self, exception=None):
        try:
            if self.context is not None:
                self.request_kwargs["_dd_context"] = self.context

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
