import inspect
from typing import Any
from typing import Optional

import wrapt

from ddtrace import tracer
from ddtrace.contrib.internal.claude_agent_sdk.utils import _extract_model_from_response
from ddtrace.contrib.internal.claude_agent_sdk.utils import _retrieve_context
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations.base_stream_handler import AsyncStreamHandler
from ddtrace.llmobs._integrations.base_stream_handler import make_traced_stream
from ddtrace.llmobs._utils import safe_json
from ddtrace.llmobs.types import Message


log = get_logger(__name__)


class CapturingAsyncIterable(wrapt.ObjectProxy):
    """Transparently wraps an AsyncIterable to capture yielded values.

    This allows us to capture prompt messages from an AsyncIterable prompt
    while still passing them through to the Claude Agent SDK.
    """

    def __init__(self, original):
        super().__init__(original)
        self._self_captured_values = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            value = await self.__wrapped__.__anext__()
            self._self_captured_values.append(value)
            return value
        except StopAsyncIteration:
            raise

    @property
    def captured_values(self):
        return self._self_captured_values


def wrap_prompt_if_async_iterable(args, kwargs):
    prompt = None
    prompt_in_args = len(args) > 0
    if prompt_in_args:
        prompt = args[0]
    else:
        prompt = kwargs.get("prompt")
    if prompt is not None and not isinstance(prompt, str):
        if hasattr(prompt, "__aiter__") or inspect.isasyncgen(prompt):
            wrapper = CapturingAsyncIterable(prompt)
            if prompt_in_args:
                args = list(args)
                args[0] = wrapper
                args = tuple(args)
            else:
                kwargs = dict(kwargs)
                kwargs["prompt"] = wrapper
            return args, kwargs, wrapper
    return args, kwargs, None


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
        self._active_tool_spans: dict[str, dict[str, Any]] = {}
        self.current_step_span = None
        self.current_llm_span = None
        self._step_response_chunk: Any = None  # deferred AssistantMessage for steps with tool calls
        self._step_input_snapshot: Optional[list[Message]] = None # input captured before llm extenstion
        self._accumulated_input_messages: Optional[list[Message]] = None
        self._create_step_span()

    def _create_step_span(self) -> None:
        """Open a step span and an llm child span for the next inference cycle."""
        self.current_step_span = self.integration.trace(
            "claude_agent_sdk.step",
            submit_to_llmobs=True,
            span_name="claude_agent_sdk.step",
            instance=self.instance,
        )
        self.current_llm_span = self.integration.trace(
            "claude_agent_sdk.llm",
            submit_to_llmobs=True,
            span_name="claude_agent_sdk.llm",
            instance=self.instance,
        )

    def _finalize_llm_span(self, chunk: Any, exception: BaseException | None = None) -> None:
        """Close the llm span with the AssistantMessage data."""
        if self.current_llm_span is None:
            return
        span = self.current_llm_span
        self.current_llm_span = None

        if self._accumulated_input_messages is None:
            self._accumulated_input_messages = self.integration.extract_llm_input_messages(
                self.request_args, self.request_kwargs, self.primary_span
            )

        self.integration.llmobs_set_tags(
            span,
            args=[],
            kwargs={"input_messages": list(self._accumulated_input_messages)},
            response=chunk,
            operation="llm",
        )
        if exception is not None:
            span.set_exc_info(type(exception), exception, exception.__traceback__)
        span.finish()

        # Extend accumulated context with the assistant's response for the next step.
        if chunk is not None:
            content = getattr(chunk, "content", []) or []
            if isinstance(content, list):
                self._accumulated_input_messages.extend(self.integration.parse_content_blocks("assistant", content))

    def _finalize_step_span(self, chunk: Any, exception: BaseException | None = None) -> None:
        if self.current_step_span is None:
            return
        span = self.current_step_span
        self.current_step_span = None

        input_msgs = self._step_input_snapshot
        if input_msgs is None:
            if self._accumulated_input_messages is None:
                self._accumulated_input_messages = self.integration.extract_llm_input_messages(
                    self.request_args, self.request_kwargs, self.primary_span
                )
            input_msgs = self._accumulated_input_messages
        self._step_input_snapshot = None

        self.integration.llmobs_set_tags(
            span,
            args=[],
            kwargs={"input_messages": list(input_msgs)},
            response=chunk,
            operation="step",
        )
        if exception is not None:
            span.set_exc_info(type(exception), exception, exception.__traceback__)
        span.finish()

    async def process_chunk(self, chunk, iterator=None):
        self.chunks.append(chunk)
        chunk_type = type(chunk).__name__

        if chunk_type == "ResultMessage" and self.instance and self.context is None:
            self.context = await _retrieve_context(self.instance)

        content = getattr(chunk, "content", []) or []

        if chunk_type == "AssistantMessage":
            if self.current_step_span is None:
                self._create_step_span()

            if self._accumulated_input_messages is None:
                self._accumulated_input_messages = self.integration.extract_llm_input_messages(
                    self.request_args, self.request_kwargs, self.primary_span
                )
            self._step_input_snapshot = list(self._accumulated_input_messages)

            self._finalize_llm_span(chunk)

            if isinstance(content, list):
                for block in content:
                    if type(block).__name__ == "ToolUseBlock":
                        tool_id = getattr(block, "id", "")
                        tool_name = getattr(block, "name", "unknown_tool")
                        tool_input = getattr(block, "input", {})

                        if self.current_step_span:
                            tracer.context_provider.activate(self.current_step_span)
                        tool_span = self.integration.trace(
                            "claude_agent_sdk.tool",
                            submit_to_llmobs=True,
                            span_name=f"claude_agent_sdk.tool.{tool_name}",
                        )
                        self._active_tool_spans[tool_id] = {
                            "tool_span": tool_span,
                            "tool_input": tool_input,
                            "tool_id": tool_id,
                        }

            # Defer or finalize the step span.
            if self._active_tool_spans:
                self._step_response_chunk = chunk
            else:
                self._finalize_step_span(chunk)

        # Tool results arrive in UserMessages
        if chunk_type == "UserMessage":
            if isinstance(content, list):
                for block in content:
                    if type(block).__name__ == "ToolResultBlock":
                        tool_use_id = getattr(block, "tool_use_id", "")
                        if tool_use_id in self._active_tool_spans:
                            tool_data = self._active_tool_spans.pop(tool_use_id)
                            result_content = getattr(block, "content", "")
                            tool_output = safe_json(result_content) or str(result_content)
                            self._finalize_tool_span(tool_data, tool_output)

            # Once all tool results are in, finalize the deferred step and open the next step+llm span
            if not self._active_tool_spans:
                if self._step_response_chunk is not None:
                    self._finalize_step_span(self._step_response_chunk)
                    self._step_response_chunk = None
                if self._accumulated_input_messages is None:
                    self._accumulated_input_messages = self.integration.extract_llm_input_messages(
                        self.request_args, self.request_kwargs, self.primary_span
                    )
                user_content = getattr(chunk, "content", []) or []
                self._accumulated_input_messages.extend(self.integration.parse_content_blocks("user", user_content))
                self._create_step_span()

    def _finalize_tool_span(self, tool_data: dict[str, Any], tool_output: str) -> None:
        tool_span = tool_data["tool_span"]

        self.integration.llmobs_set_tags(
            tool_span,
            args=[],
            kwargs={
                "tool_input": tool_data["tool_input"],
                "tool_output": tool_output,
                "tool_id": tool_data.get("tool_id", ""),
            },
            response=None,
            operation="tool",
        )

        tool_span.finish()

    def finalize_stream(self, exception=None):
        try:
            # Finalize any open llm span first.
            if self.current_llm_span is not None:
                self._finalize_llm_span(None, exception=exception)

            # Finalize any open or deferred step span.
            if self._step_response_chunk is not None and self.current_step_span is not None:
                self._finalize_step_span(self._step_response_chunk, exception=exception)
                self._step_response_chunk = None
            elif self.current_step_span is not None:
                self._finalize_step_span(None, exception=exception)

            model = _extract_model_from_response(self.chunks)
            if model:
                self.primary_span._set_attribute("claude_agent_sdk.request.model", model)
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
