from claude_agent_sdk import ResultMessage

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations.base_stream_handler import AsyncStreamHandler
from ddtrace.llmobs._integrations.base_stream_handler import make_traced_stream
from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanKind
from ddtrace.ext import net


log = get_logger(__name__)


class ClaudeAgentAsyncStreamHandler(AsyncStreamHandler):
    """Handler for tracing claude_agent_sdk async message streams."""

    def __init__(self, integration, span, args, kwargs, instance=None, **options):
        super().__init__(integration, span, args, kwargs, **options)
        self.instance = instance
        self.span_finished = False

    async def process_chunk(self, chunk, iterator=None):
        self.chunks.append(chunk)

        # Close span when we receive a ResultMessage
        if isinstance(chunk, ResultMessage) and not self.span_finished:
            self._finish_span()

    def _finish_span(self):
        if self.span_finished:
            return

        try:
            self.integration.llmobs_set_tags(
                self.primary_span,
                args=self.request_args,
                kwargs=self.request_kwargs,
                response=self.chunks,
                operation="request"
            )
            self.primary_span.finish()
            self.span_finished = True

            # Clean up instance attributes
            if self.instance is not None:
                self.instance._datadog_span = None
                self.instance._datadog_query_args = None
                self.instance._datadog_query_kwargs = None
        except Exception:
            log.warning("Error finishing claude_agent_sdk span", exc_info=True)

    def finalize_stream(self, exception=None):
        if exception and not self.span_finished:
            self.primary_span.set_exc_info(exception.__class__, exception, exception.__traceback__)

        # Finish span if not already closed (e.g., stream exhausted without ResultMessage)
        if not self.span_finished:
            self._finish_span()


class QueryStreamHandler(AsyncStreamHandler):
    """Handler for tracing standalone query() async streams."""

    def __init__(self, integration, span, args, kwargs, operation, **options):
        super().__init__(integration, span, args, kwargs, **options)
        self.operation = operation

    async def process_chunk(self, chunk, iterator=None):
        self.chunks.append(chunk)

    def finalize_stream(self, exception=None):
        if exception:
            self.primary_span.set_exc_info(exception.__class__, exception, exception.__traceback__)

        try:
            self.integration.llmobs_set_tags(
                self.primary_span,
                args=self.request_args,
                kwargs=self.request_kwargs,
                response=self.chunks,
                operation=self.operation
            )
            self.primary_span.finish()
        except Exception:
            log.warning("Error finishing claude_agent_sdk query span", exc_info=True)

def handle_receive_messages_stream(integration, instance, agen, span, query_args, query_kwargs):
    """Wrap receive_messages() async generator with tracing."""
    handler = ClaudeAgentAsyncStreamHandler(
        integration=integration,
        span=span,
        args=query_args,
        kwargs=query_kwargs,
        instance=instance
    )

    return make_traced_stream(agen, handler)

def handle_query_stream(integration, pin, func, args, kwargs, operation_name, span_name, operation, instance=None):
    """Wrap standalone query() async generator with tracing."""
    span = integration.trace(pin, operation_name, submit_to_llmobs=True, span_name=span_name, model="", instance=instance)

    span._set_tag_str(SPAN_KIND, SpanKind.CLIENT)
    span._set_tag_str(net.TARGET_HOST, "api.anthropic.com")

    try:
        agen = func(*args, **kwargs)
    except Exception as e:
        span.set_exc_info(e.__class__, e, e.__traceback__)
        span.finish()
        raise

    handler = QueryStreamHandler(
        integration=integration,
        span=span,
        args=args,
        kwargs=kwargs,
        operation=operation
    )

    return make_traced_stream(agen, handler)
