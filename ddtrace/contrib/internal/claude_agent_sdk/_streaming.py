from dataclasses import dataclass
import inspect
from typing import Any
from typing import Optional

import wrapt

from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_TYPE
from ddtrace.contrib.internal.claude_agent_sdk.utils import _extract_model_from_response
from ddtrace.contrib.internal.claude_agent_sdk.utils import _retrieve_context
from ddtrace.contrib.internal.claude_agent_sdk.utils import extract_partial_message_usage
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.llmobs._integrations.base_stream_handler import AsyncStreamHandler
from ddtrace.llmobs._integrations.base_stream_handler import make_traced_stream
from ddtrace.llmobs._utils import add_span_link
from ddtrace.llmobs._utils import safe_json
from ddtrace.llmobs.types import Message


log = get_logger(__name__)


@dataclass
class _SpanRef:
    span_id: str
    trace_id: str


@dataclass
class _MergedAssistantMessage:
    """A synthetic AssistantMessage that merges several chunks sharing a message_id. This prevents
    the same usage block from being counted on more than one LLM span. Exposes the same
    attributes the integration reads off a real ``AssistantMessage`` (``content``, ``model``, ``usage``,
    ``error``).
    """

    content: list
    model: str
    usage: Optional[dict]
    error: Any
    message_id: Optional[str]


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


def _is_partial_stream_noise(chunk) -> bool:
    """Whether a chunk is one of the events we inject by forcing include_partial_messages.

    Only the ``StreamEvent`` partials and the ``stream_request_start`` ping
    (``SystemMessage(subtype="status")`` with ``data["status"] == "requesting"``) are ours.
    Other ``status`` messages (compaction results/errors, permission-mode changes) are
    caller-visible and not gated on partial messages, so we must let them through.
    """
    chunk_type = type(chunk).__name__
    if chunk_type == "StreamEvent":
        return True
    if chunk_type == "SystemMessage" and getattr(chunk, "subtype", None) == "status":
        data = getattr(chunk, "data", None)
        return isinstance(data, dict) and data.get("status") == "requesting"
    return False


async def filter_forced_partial_noise(resp):
    """Strip the events we injected from an otherwise untraced receive stream.

    The ``connect(prompt=...)`` shortcut dispatches its prompt without a ``query()`` call, so
    that stream has no span/handler to trace or filter it. When we forced partial streaming on
    such a client, we still must not surface the extra events — enabling ddtrace should never
    change the caller's message stream. This is a passthrough that drops only those events.
    """
    async for chunk in resp:
        if _is_partial_stream_noise(chunk):
            continue
        yield chunk


def handle_streamed_response(integration, resp, args, kwargs, span, operation, instance=None, filter_partial=False):
    return make_traced_stream(
        resp,
        ClaudeAgentSdkAsyncStreamHandler(
            integration, span, args, kwargs, operation=operation, instance=instance, filter_partial=filter_partial
        ),
    )


class ClaudeAgentSdkAsyncStreamHandler(AsyncStreamHandler):
    def __init__(self, integration, span, args, kwargs, operation, instance=None, filter_partial=False):
        super().__init__(integration, span, args, kwargs)
        self.operation = operation
        self.instance = instance
        # Indicates whether we enabled include_partial_messages ourselves
        self._filter_partial = filter_partial
        # Per-turn usage mined from the partial-message stream, keyed by message_id:
        # message_start seeds the input/cache tokens, each message_delta updates the true
        # output tokens. On SDK versions without AssistantMessage.usage (< 0.1.49) this is
        # the only token source; on newer versions it corrects the output snapshot.
        self._partial_usage_by_id: dict[str, dict] = {}
        # The message id currently streaming, keyed by parent_tool_use_id scope: None for the main
        # agent, the spawning tool-use id for a subagent. Scoping keeps concurrent subagent runs,
        # whose StreamEvents interleave in one stream, from clobbering each other's cursor when an
        # id-less message_delta is attributed back to its message_start.
        self._partial_current_id_by_scope: dict[Optional[str], str] = {}
        self.context = None
        self._active_tool_spans: dict[str, dict[str, Any]] = {}
        self.current_step_span = None
        self.current_llm_span = None
        self._step_response_chunk: Any = None  # deferred AssistantMessage for steps with tool calls
        self._step_input_snapshot: Optional[list[Message]] = None  # input captured before llm extension
        self._accumulated_input_messages: Optional[list[Message]] = None
        # Buffer of consecutive AssistantMessage chunks that share a message_id.
        # The turn is flushed (one llm span emitted) when a chunk with a different
        # message_id arrives, or a UserMessage/ResultMessage ends the turn.
        self._pending_chunks: list[Any] = []
        self._pending_message_id: Optional[str] = None
        # The streaming turn id captured when the pending turn started buffering. Used to join
        # partial usage back to the turn on SDKs where AssistantMessage has no message_id.
        self._pending_partial_id: Optional[str] = None
        self._is_finalized = False
        # Refs are (span_id, trace_id) snapshots and are used to chain together
        # step spans as well as llm → tool → llm spans.
        self._last_llm_span_ref: Optional[_SpanRef] = None
        self._last_step_span_ref: Optional[_SpanRef] = None
        self._step_tool_span_refs: list[_SpanRef] = []
        self._create_step_span()

    def _is_forced_partial_noise(self, chunk) -> bool:
        """Chunks we turned on ourselves and must not surface to the caller or store."""
        return self._filter_partial and _is_partial_stream_noise(chunk)

    def _capture_partial_usage(self, chunk) -> None:
        """Record per-turn token usage from a StreamEvent's raw Anthropic event.

        ``message_start`` seeds the turn's input/cache tokens; each ``message_delta``
        updates the running true output tokens (the last one seen wins). The id-less
        ``message_delta`` is joined back to its ``message_start`` within the same
        ``parent_tool_use_id`` scope, so concurrent subagent streams stay separate.
        """
        signal = extract_partial_message_usage(getattr(chunk, "event", None))
        if signal is None:
            return
        scope = getattr(chunk, "parent_tool_use_id", None)
        message_id, usage = signal
        if message_id is not None:
            self._partial_current_id_by_scope[scope] = message_id
            self._partial_usage_by_id[message_id] = dict(usage)
        elif usage:
            current_id = self._partial_current_id_by_scope.get(scope)
            if current_id is not None:
                self._partial_usage_by_id.setdefault(current_id, {}).update(usage)

    def should_yield_chunk(self, chunk) -> bool:
        return not self._is_forced_partial_noise(chunk)

    async def process_chunk(self, chunk, iterator=None):
        chunk_type = type(chunk).__name__

        if chunk_type == "StreamEvent":
            self._capture_partial_usage(chunk)

        # Keep the events we injected out of chunk storage so span extraction is unaffected.
        if self._is_forced_partial_noise(chunk):
            return

        self.chunks.append(chunk)

        if chunk_type == "ResultMessage":
            if self.instance and self.context is None:
                self.context = await _retrieve_context(self.instance)
            # eagerly finish when the result message is received since
            # the generator may be left open indefinitely
            self.finalize_stream()

        content = getattr(chunk, "content", []) or []

        if chunk_type == "AssistantMessage":
            self._handle_assistant_message(chunk, content)

        # Tool results arrive in UserMessages
        if chunk_type == "UserMessage":
            self._handle_user_message(chunk, content)

    def finalize_stream(self, exception=None):
        if self._is_finalized:
            return
        self._is_finalized = True
        try:
            # Flush the last buffered model turn (its llm/step spans and any tool spans).
            self._flush_pending_turn()

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

            # Finalize incomplete tools so they emit the same span data as completed ones.
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

    def _snapshot(self, span) -> _SpanRef:
        """Capture span_id and trace_id as plain strings — decoupled from the Span object lifetime."""
        return _SpanRef(span_id=str(span.span_id), trace_id=format_trace_id(span.trace_id))

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
            activate=False,  # spans the caller starts while iterating should not nest under this span
        )
        self._keep_step_active_in_llmobs()
        # Link each new llm span from the previous tool outputs (fan-in), or from the previous llm span if no tools ran.
        if self._step_tool_span_refs:
            for tool_ref in self._step_tool_span_refs:
                add_span_link(self.current_llm_span, tool_ref.span_id, tool_ref.trace_id, "output", "input")
            self._step_tool_span_refs.clear()
        elif self._last_llm_span_ref is not None:
            add_span_link(
                self.current_llm_span,
                self._last_llm_span_ref.span_id,
                self._last_llm_span_ref.trace_id,
                "output",
                "input",
            )

        # Link each new step span from the previous step span.
        if self._last_step_span_ref is not None:
            add_span_link(
                self.current_step_span,
                self._last_step_span_ref.span_id,
                self._last_step_span_ref.trace_id,
                "output",
                "input",
            )

    def _keep_step_active_in_llmobs(self) -> None:
        """Re-activate the step span as the active LLMObs span.

        Creating the llm (or a tool) span makes that leaf the active LLMObs span, so anything
        opened while the turn is still buffering would parent under it instead of the step.
        Re-activating the step keeps it the parent.
        """
        if self.current_step_span is None or not self.integration.llmobs_enabled:
            return
        from ddtrace.llmobs import LLMObs

        if LLMObs._instance is not None:
            LLMObs._instance._llmobs_context_provider.activate(self.current_step_span)

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
        self._last_llm_span_ref = self._snapshot(span)
        span.finish()

        # Extend accumulated context with the assistant's response for the next step.
        if chunk is not None:
            content = getattr(chunk, "content", []) or []
            if isinstance(content, list):
                self._accumulated_input_messages.extend(self.integration.parse_content_blocks("assistant", content))

    def _finalize_step_span(self, chunk: Any, exception: BaseException | None = None) -> None:
        if self.current_step_span is None:
            log.debug("_finalize_step_span for claude agent sdk called with no step span active")
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
        self._last_step_span_ref = self._snapshot(span)
        span.finish()

    def _finalize_tool_span(self, tool_data: dict[str, Any], tool_output: str, is_error: bool = False) -> None:
        tool_span = tool_data["tool_span"]

        if is_error:
            tool_span.error = 1
            tool_span.set_tag(ERROR_TYPE, "ToolError")
            tool_span.set_tag(ERROR_MSG, tool_output)

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

        self._step_tool_span_refs.append(self._snapshot(tool_span))
        tool_span.finish()

    def _handle_assistant_message(self, chunk: Any, content: Any) -> None:
        """Buffer the chunk, deduping by message_id so one model turn maps to one llm span.

        AIDEV-NOTE: The SDK may split one model turn (e.g. a text block plus a tool_use
        block) into several AssistantMessage chunks that each repeat the same message-level
        usage. Buffering and merging chunks of one message into a single llm span (flushed on
        turn change / UserMessage / ResultMessage) keeps token counts from being
        double-counted. Newer SDKs (>= 0.1.49) join the chunks by AssistantMessage.message_id;
        older SDKs omit that field, so we join by the streaming message id from message_start
        (unchanged until the next message_start), giving the same one-span-per-message result.
        """
        incoming_id = getattr(chunk, "message_id", None)
        # The streaming message id currently active in this message's own subagent scope.
        current_partial_id = self._partial_current_id_by_scope.get(getattr(chunk, "parent_tool_use_id", None))

        # Same turn continued: accumulate onto the buffered chunks (usage counted once). Match
        # on message_id when the SDK provides it, otherwise on the streaming message id, which
        # stays constant across a message's chunks until the next message_start.
        same_turn = self._pending_chunks and (
            (incoming_id is not None and incoming_id == self._pending_message_id)
            or (
                incoming_id is None
                and current_partial_id is not None
                and current_partial_id == self._pending_partial_id
            )
        )
        if same_turn:
            self._pending_chunks.append(chunk)
            self._open_tool_spans(content)
            return

        # New turn: flush the previous one, then start buffering this chunk.
        self._flush_pending_turn()
        if self.current_step_span is None:
            self._create_step_span()
        self._pending_chunks = [chunk]
        self._pending_message_id = incoming_id
        # Stamp the turn currently streaming so the usage can be joined back to it even when
        # the SDK gives the AssistantMessage no message_id.
        self._pending_partial_id = current_partial_id
        self._open_tool_spans(content)

    def _open_tool_spans(self, content: Any) -> None:
        """Open a tool span for each ToolUseBlock in a chunk's content."""
        if not isinstance(content, list):
            return
        for block in content:
            if type(block).__name__ == "ToolUseBlock":
                self._handle_tool_use_block(block)

    def _flush_pending_turn(self) -> None:
        """Emit the llm/step spans for the buffered AssistantMessage chunks, if any."""
        if not self._pending_chunks:
            return
        chunks = self._pending_chunks
        pending_partial_id = self._pending_partial_id
        self._pending_chunks = []
        self._pending_message_id = None
        self._pending_partial_id = None

        if self.current_step_span is None:
            self._create_step_span()

        if self._accumulated_input_messages is None:
            self._accumulated_input_messages = self.integration.extract_llm_input_messages(
                self.request_args, self.request_kwargs, self.primary_span
            )
        self._step_input_snapshot = list(self._accumulated_input_messages)

        response = self._merge_assistant_chunks(chunks)
        # Older SDKs (< 0.1.49) don't put a message_id on AssistantMessage, so fall back to the
        # streaming turn id stamped when buffering began, which is how the usage was keyed.
        turn_id = getattr(chunks[0], "message_id", None) or pending_partial_id
        response = self._apply_partial_usage(response, turn_id)

        self._finalize_llm_span(response)

        # Defer or finalize the step span.
        if self._active_tool_spans:
            self._step_response_chunk = response
        else:
            self._finalize_step_span(response)

    def _apply_partial_usage(self, response: Any, message_id: Optional[str]) -> Any:
        """Reconcile the turn's usage with the true counts mined from the stream events.

        ``response`` is always a fresh ``_MergedAssistantMessage`` with its own usage dict
        (see ``_merge_assistant_chunks``), so we mutate it in place without touching the
        message object the caller received from the stream.

        When the SDK reported its own usage (``AssistantMessage.usage``, >= 0.1.49) we trust
        its input/cache counts and only correct ``output_tokens`` from the deltas (the SDK's
        value is a pre-generation snapshot). When it did not (< 0.1.49), the stream events
        are the only source, so we synthesize the whole usage block from them.
        """
        if message_id is None:
            return response
        partial = self._partial_usage_by_id.get(message_id)
        if not partial:
            return response
        usage = getattr(response, "usage", None)
        if isinstance(usage, dict) and usage:
            true_output = partial.get("output_tokens")
            if true_output is not None:
                usage["output_tokens"] = true_output
        else:
            response.usage = dict(partial)
        return response

    def _merge_assistant_chunks(self, chunks: list) -> Any:
        """Combine AssistantMessage chunks sharing a message_id into one response object.

        Always returns a fresh ``_MergedAssistantMessage`` (never an SDK chunk), so later
        corrections like ``_apply_partial_usage`` can mutate it safely without affecting the
        message object the caller received from the stream.
        """
        merged_content: list = []
        usage: Optional[dict] = None
        error: Any = None
        model = ""
        message_id: Optional[str] = None
        for c in chunks:
            merged_content.extend(getattr(c, "content", []) or [])
            # All chunks of one message repeat the same message-level usage; keep a copy of
            # the last non-empty one (most complete) so it is counted exactly once and our
            # later corrections never reach the SDK's own usage dict.
            if getattr(c, "usage", None):
                usage = dict(c.usage)
            if error is None:
                error = getattr(c, "error", None)
            if not model:
                model = getattr(c, "model", "") or ""
            if message_id is None:
                message_id = getattr(c, "message_id", None)
        return _MergedAssistantMessage(
            content=merged_content, model=model, usage=usage, error=error, message_id=message_id
        )

    def _handle_user_message(self, chunk: Any, content: Any) -> None:
        """Finalize tool spans for any tool results and, once all are in, close the deferred step span."""
        # A UserMessage (tool results) ends the current model turn
        self._flush_pending_turn()

        if isinstance(content, list):
            for block in content:
                if type(block).__name__ == "ToolResultBlock":
                    self._handle_tool_result_block(block)

        # Once all tool results are in, finalize the deferred step and open the next step+llm span
        if not self._active_tool_spans and self._step_response_chunk is not None:
            self._finalize_step_span(self._step_response_chunk)
            self._step_response_chunk = None
            if self._accumulated_input_messages is None:
                self._accumulated_input_messages = self.integration.extract_llm_input_messages(
                    self.request_args, self.request_kwargs, self.primary_span
                )
            user_content = getattr(chunk, "content", []) or []
            self._accumulated_input_messages.extend(self.integration.parse_content_blocks("user", user_content))
            self._create_step_span()

    def _handle_tool_use_block(self, block: Any) -> None:
        """Open a tool span for a ToolUseBlock and register it as awaiting a result."""
        tool_id = getattr(block, "id", "")
        tool_name = getattr(block, "name", "unknown_tool")
        tool_input = getattr(block, "input", {})
        tool_span = self.integration.trace(
            "claude_agent_sdk.tool",
            submit_to_llmobs=True,
            span_name=f"claude_agent_sdk.tool.{tool_name}",
            activate=False,
        )
        # Link from the current turn's llm span, which is still open (not yet finalized) when
        # tools are opened eagerly. Fall back to the last finalized llm span if it is gone.
        llm_ref = (
            self._snapshot(self.current_llm_span) if self.current_llm_span is not None else self._last_llm_span_ref
        )
        if llm_ref is not None:
            add_span_link(
                tool_span,
                llm_ref.span_id,
                llm_ref.trace_id,
                "output",
                "input",
            )
        self._active_tool_spans[tool_id] = {
            "tool_span": tool_span,
            "tool_input": tool_input,
            "tool_id": tool_id,
        }
        self._keep_step_active_in_llmobs()

    def _handle_tool_result_block(self, block: Any) -> None:
        """Finalize the matching tool span for a ToolResultBlock if one is active."""
        tool_use_id = getattr(block, "tool_use_id", "")
        if tool_use_id not in self._active_tool_spans:
            return
        tool_data = self._active_tool_spans.pop(tool_use_id)
        result_content = getattr(block, "content", "")
        tool_output = safe_json(result_content) or str(result_content)
        is_error = getattr(block, "is_error", False) or False
        self._finalize_tool_span(tool_data, tool_output, is_error=is_error)
