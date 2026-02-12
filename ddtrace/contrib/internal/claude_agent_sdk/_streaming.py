from typing import Any

from ddtrace.contrib.internal.claude_agent_sdk.utils import _extract_model_from_response
from ddtrace.contrib.internal.claude_agent_sdk.utils import _retrieve_context
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations.base_stream_handler import AsyncStreamHandler
from ddtrace.llmobs._integrations.base_stream_handler import make_traced_stream
from ddtrace.llmobs._utils import safe_json


log = get_logger(__name__)


def handle_streamed_response(integration, resp, args, kwargs, span, operation, pin, instance=None):
    return make_traced_stream(
        resp,
        ClaudeAgentSdkAsyncStreamHandler(
            integration, span, args, kwargs, operation=operation, pin=pin, instance=instance
        ),
    )


class ClaudeAgentSdkAsyncStreamHandler(AsyncStreamHandler):
    def __init__(self, integration, span, args, kwargs, operation, pin, instance=None):
        super().__init__(integration, span, args, kwargs)
        self.operation = operation
        self.pin = pin
        self.instance = instance
        self.context = None
        self._active_tool_spans: dict[str, dict[str, Any]] = {}

    async def process_chunk(self, chunk, iterator=None):
        self.chunks.append(chunk)

        if type(chunk).__name__ == "ResultMessage" and self.instance and self.context is None:
            self.context = await _retrieve_context(self.instance)

        # Handle tool use and result blocks for tool span creation
        content = getattr(chunk, "content", []) or []
        if not isinstance(content, list):
            return

        for block in content:
            block_type = type(block).__name__

            if block_type == "ToolUseBlock":
                tool_id = getattr(block, "id", "")
                tool_name = getattr(block, "name", "unknown_tool")
                tool_input = getattr(block, "input", {})

                tool_span = self.integration.trace(
                    self.pin,
                    tool_name,
                    submit_to_llmobs=True,
                    span_name=f"claude_agent_sdk.tool.{tool_name}",
                )

                self._active_tool_spans[tool_id] = {
                    "tool_span": tool_span,
                    "tool_input": tool_input,
                }
            if block_type == "ToolResultBlock":
                tool_use_id = getattr(block, "tool_use_id", "")
                if tool_use_id in self._active_tool_spans:
                    tool_data = self._active_tool_spans.pop(tool_use_id)
                    result_content = getattr(block, "content", "")
                    tool_output = safe_json(result_content) or str(result_content)
                    self._finalize_tool_span(tool_data, tool_output)

    def _finalize_tool_span(self, tool_data: dict[str, Any], tool_output: str) -> None:
        tool_span = tool_data["tool_span"]

        self.integration.llmobs_set_tags(
            tool_span,
            args=[],
            kwargs={
                "tool_input": tool_data["tool_input"],
                "tool_output": tool_output,
            },
            response=None,
            operation="tool",
        )

        tool_span.finish()

    def finalize_stream(self, exception=None):
        try:
            model = _extract_model_from_response(self.chunks)
            if model:
                self.primary_span._set_tag_str("claude_agent_sdk.request.model", model)
            if self.context is not None:
                self.request_kwargs["_dd_context"] = self.context

            self.integration.llmobs_set_tags(
                self.primary_span,
                args=self.request_args,
                kwargs=self.request_kwargs,
                response=self.chunks if self.chunks else None,
                operation=self.operation,
            )

            # Fallback to handle any incomplete tool spans (tools that didn't have a ToolResultBlock)
            if self._active_tool_spans:
                log.debug(
                    "Finishing %d incomplete tool spans without results",
                    len(self._active_tool_spans),
                )
                for tool_id, tool_data in list(self._active_tool_spans.items()):
                    try:
                        self._finalize_tool_span(tool_data, tool_output="")
                    except Exception:
                        log.warning("Error finishing incomplete tool span for tool_id %s", tool_id, exc_info=True)
                self._active_tool_spans.clear()
        except Exception:
            log.warning("Error processing claude_agent_sdk stream response.", exc_info=True)
        finally:
            self.primary_span.finish()
