from typing import Any

import agents
from agents.tracing.processor_interface import TracingProcessor
from agents.tracing.spans import Span as OaiSpan
from agents.tracing.traces import Trace as OaiTrace

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.llmobs._integrations.openai_agents import split_message_chars
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
        # finishing. Snapshots were populated by on_span_end (response-type spans, LLM side)
        # and by patch.py's agent-side wrappers — ``_patched_run_single_turn`` for agents
        # versions where the per-turn function is an instance method (Runner/AgentRunner)
        # and ``_patched_run_single_turn_module`` for agents >= ~0.10 where it moved to a
        # module-level free function in agents.run_internal.run_loop.
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
        # for the context_delta payload. Tokens come from the model's reported usage;
        # per-category chars are derived from the system instructions + role-split message
        # history. split_message_chars lives in openai_agents.py alongside count_tools_chars.
        if span_adapter.span_type == "response":
            metrics = span_adapter.llmobs_metrics or {}
            input_tokens = metrics.get("input_tokens", 0)
            if input_tokens > 0:
                system_chars = len(span_adapter.response_system_instructions or "")
                user_chars, assistant_chars = split_message_chars(span_adapter.input)
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
