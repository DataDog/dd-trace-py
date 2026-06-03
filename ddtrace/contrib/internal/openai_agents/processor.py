from typing import Any

import agents
from agents.tracing.processor_interface import TracingProcessor
from agents.tracing.spans import Span as OaiSpan
from agents.tracing.traces import Trace as OaiTrace

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.llmobs._integrations.utils import OaiSpanAdapter
from ddtrace.llmobs._integrations.utils import OaiTraceAdapter


logger = get_logger(__name__)


class LLMObsTraceProcessor(TracingProcessor):
    def __init__(self, integration):
        super().__init__()
        self._integration = integration

    def on_span_start(self, span: OaiSpan[Any]) -> None:
        """Called when a span starts.

        Args:
            span: The span that started.
        """
        if not getattr(agents, "_datadog_patch", False):
            return

        oai_span = OaiSpanAdapter(span)
        if not oai_span.llmobs_span_kind:
            return
        self._integration.trace(oai_span=oai_span, submit_to_llmobs=True)

    def on_trace_start(self, trace: OaiTrace) -> None:
        """Called when a trace starts.

        Args:
            trace: The trace that started.
        """
        if not getattr(agents, "_datadog_patch", False):
            return

        self._integration.trace(oai_trace=OaiTraceAdapter(trace), submit_to_llmobs=True)

    def on_trace_end(self, trace: OaiTrace) -> None:
        """Called when a trace is finished.

        Args:
            trace: The trace that started.
        """
        if not getattr(agents, "_datadog_patch", False):
            return

        trace_adapter = OaiTraceAdapter(trace)
        trace_root_span = self._integration.oai_to_llmobs_span.get(trace_adapter.trace_id)
        if not trace_root_span:
            logger.warning("No LLMObs span found for openai trace %s", trace_adapter.trace_id)
            return
        self._integration.llmobs_set_tags(
            trace_root_span,
            [],
            {"oai_trace": trace_adapter},
        )
        # MLOB-7584: emit the assembled context_delta onto the agent-kind root span before
        # finishing. Snapshots were populated by on_span_end (response-type spans, llm side)
        # and by _patched_run_single_turn in patch.py (agent side).
        self._integration.emit_context_delta(trace_root_span, trace_root_span.trace_id)
        self._integration.llmobs_traces.pop(format_trace_id(trace_root_span.trace_id), None)
        trace_root_span.finish()

    def on_span_end(self, span: OaiSpan[Any]) -> None:
        """Called when a span is finished. Should not block or raise exceptions.

        Args:
            span: The span that finished.
        """
        if not getattr(agents, "_datadog_patch", False):
            return

        span_adapter = OaiSpanAdapter(span)
        llmobs_span = self._integration.oai_to_llmobs_span.get(span_adapter.span_id)
        if not llmobs_span:
            return
        self._integration.llmobs_set_tags(llmobs_span, [], {"oai_span": span_adapter})

        # MLOB-7584: on response-type spans (LLM calls), capture per-call context categories
        # for the Context Visualization. Tokens come from the model's reported usage; per-category
        # chars are derived from the system instructions + role-split message history.
        if span_adapter.span_type == "response":
            metrics = span_adapter.llmobs_metrics or {}
            input_tokens = metrics.get("input_tokens", 0)
            if input_tokens > 0:
                system_chars = len(span_adapter.response_system_instructions or "")
                user_chars, assistant_chars = _split_message_chars(span_adapter.input)
                self._integration.record_llm_side(
                    llmobs_span.trace_id,
                    input_tokens=input_tokens,
                    system_chars=system_chars,
                    user_chars=user_chars,
                    assistant_chars=assistant_chars,
                    model=span_adapter.llmobs_model_name or "",
                )

        llmobs_span.finish()

    def force_flush(self) -> None:
        pass

    def shutdown(self) -> None:
        self._integration.clear_state()


def _split_message_chars(messages: Any) -> tuple[int, int]:
    """Return ``(user_chars, assistant_chars)`` for a response-span's ``input`` payload.

    AIDEV-NOTE: MLOB-7584 — OaiSpanAdapter.input is typed as ``str | list[Any]``. When the
    initial user prompt is the only input, it arrives as a string and we bucket all of it
    into user_chars. When the agent loop has run for ≥1 turn, the message list contains
    role-tagged dicts/objects and we split by ``role``.
    """
    if isinstance(messages, str):
        return len(messages), 0
    if not isinstance(messages, list):
        return 0, 0
    user_chars = 0
    assistant_chars = 0
    for msg in messages:
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
        chars = len(str(content)) if content is not None else 0
        if role == "user":
            user_chars += chars
        elif role == "assistant":
            assistant_chars += chars
    return user_chars, assistant_chars
