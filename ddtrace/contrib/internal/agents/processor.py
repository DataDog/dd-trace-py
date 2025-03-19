from typing import Any
from typing import Optional

from agents.tracing.processor_interface import TracingProcessor
from agents.tracing.spans import Span as OaiSpan
from agents.tracing.traces import Trace as OaiTrace

from ddtrace._trace.span import Span as DdSpan
from ddtrace.contrib.internal.agents.utils import LLMObsTraceInfo
from ddtrace.contrib.internal.agents.utils import ToolCallTracker
from ddtrace.contrib.internal.agents.utils import _load_value
from ddtrace.contrib.internal.agents.utils import _process_input_messages
from ddtrace.contrib.internal.agents.utils import _process_output_messages
from ddtrace.contrib.internal.agents.utils import add_span_link
from ddtrace.contrib.internal.agents.utils import get_span_kind_from_span
from ddtrace.contrib.internal.agents.utils import set_error_on_span
from ddtrace.contrib.internal.agents.utils import start_span_fn
from ddtrace.contrib.internal.agents.utils import trace_input_from_response_span
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import NAME
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import PARENT_ID_KEY
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._utils import _get_nearest_llmobs_ancestor
from ddtrace.llmobs._utils import _get_span_name


logger = get_logger(__name__)


class LLMObsTraceProcessor(TracingProcessor):
    def __init__(self):
        super().__init__()
        self.oai_to_llmobs_span = {}  # type: Dict[str, DdSpan]
        self.llmobs_traces = {}  # type: Dict[str, LLMObsTraceInfo]
        self.tool_tracker = ToolCallTracker()

    def get_trace_info(self, span: OaiSpan[Any]) -> Optional[LLMObsTraceInfo]:
        """Get trace info for a span safely.

        Args:
            oai_span: The span to get trace info for.

        Returns:
            The trace info if found, None otherwise.
        """
        llmobs_span = self.oai_to_llmobs_span.get(span.span_id)
        if not llmobs_span:
            return None

        return self.llmobs_traces.get(LLMObs.export_span(llmobs_span)["trace_id"])

    def on_span_start(self, span: OaiSpan[Any]) -> None:
        """Called when a span starts.

        Args:
            span: The span that started.
        """
        span_name = span.export().get("span_data", {}).get("name")
        span_kind = get_span_kind_from_span(span)

        if span_kind == "llm":
            cur_span = LLMObs.llm(
                name=span_name if span_name else "openai.response",
                model_name="default",
                model_provider="openai",
            )
            parent = _get_nearest_llmobs_ancestor(cur_span)
            if parent and parent._get_ctx_item(SPAN_KIND) == "agent" and _get_span_name(parent):
                cur_span._set_ctx_item(NAME, _get_span_name(parent) + " (LLM)")
        else:
            cur_span = start_span_fn(span_kind)(name=span_name)

        self.oai_to_llmobs_span[span.span_id] = cur_span
        trace_info = self.get_trace_info(span)

        if trace_info and span_kind == "agent" and cur_span._get_ctx_item(PARENT_ID_KEY) == trace_info.span_id:
            trace_info.current_top_level_agent_span_id = LLMObs.export_span(cur_span)["span_id"]

        if trace_info and span_kind == "llm" and not trace_info.input_oai_span:
            trace_info.input_oai_span = span
            add_span_link(
                cur_span,
                trace_info.span_id,
                trace_info.trace_id,
                "input",
                "input",
            )

    def on_trace_start(self, trace: OaiTrace) -> None:
        """Called when a trace starts.

        Args:
            trace: The trace that started.
        """
        workflow_name = trace.name
        group_id = trace.export().get("group_id")
        args = {"name": workflow_name if workflow_name else "Agent workflow"}
        if group_id:
            args["session_id"] = group_id

        root_llmobs_workflow = LLMObs.workflow(**args)
        self.oai_to_llmobs_span[trace.trace_id] = root_llmobs_workflow

        if trace.export().get("metadata"):
            root_llmobs_workflow._set_ctx_item(METADATA, trace.export().get("metadata"))

        self.llmobs_traces[LLMObs.export_span(root_llmobs_workflow)["trace_id"]] = LLMObsTraceInfo(
            **LLMObs.export_span(root_llmobs_workflow)
        )

    def on_trace_end(self, trace: OaiTrace) -> None:
        """Called when a trace is finished.

        Args:
            trace: The trace that started.
        """
        cur_trace = self.oai_to_llmobs_span.get(trace.trace_id)
        if not cur_trace:
            return
        trace_info = self.llmobs_traces.get(LLMObs.export_span(cur_trace)["trace_id"])
        if not trace_info or not trace_info.input_oai_span or not trace_info.output_oai_span:
            cur_trace.finish()
            return

        if trace_info.input_oai_span:
            input_value = trace_input_from_response_span(trace_info.input_oai_span)
            if input_value:
                cur_trace._set_ctx_item(INPUT_VALUE, input_value)

        if trace_info.output_oai_span:
            output_value = trace_info.output_oai_span.span_data.response.output_text
            if output_value:
                cur_trace._set_ctx_item(OUTPUT_VALUE, output_value)

            add_span_link(
                cur_trace,
                LLMObs.export_span(self.oai_to_llmobs_span[trace_info.output_oai_span.span_id])["span_id"],
                trace_info.trace_id,
                "output",
                "output",
            )
        cur_trace.finish()

    def on_span_end(self, span: OaiSpan[Any]) -> None:
        """Called when a span is finished. Should not block or raise exceptions.

        Args:
            span: The span that finished.
        """
        llmobs_span = self.oai_to_llmobs_span.get(span.span_id)
        if not llmobs_span:
            return

        span_kind = get_span_kind_from_span(span)
        trace_info = self.get_trace_info(span)

        if trace_info:
            current_top_level_agent_span_id = trace_info.current_top_level_agent_span_id
            if (
                span_kind == "llm"
                and current_top_level_agent_span_id
                and llmobs_span._get_ctx_item(PARENT_ID_KEY) == current_top_level_agent_span_id
            ):
                trace_info.output_oai_span = span

        self.set_span_attributes(span, llmobs_span)
        llmobs_span.finish()

    def force_flush(self) -> bool:
        """Force flush of data to target. Should never throw exception.

        Returns:
            True if the flush succeeded, False otherwise.
        """
        return True

    def shutdown(self) -> None:
        """Shuts down the processor."""
        self.oai_to_llmobs_span.clear()
        self.llmobs_traces.clear()
        self.tool_tracker = ToolCallTracker()  # Reset tool tracker

    def set_span_attributes_response(self, span_data, llmobs_span) -> None:
        """Sets attributes for response type spans."""
        if not hasattr(span_data, "response"):
            return

        if hasattr(span_data.response, "model"):
            llmobs_span._set_ctx_item(MODEL_NAME, span_data.response.model)
            llmobs_span._set_ctx_item(MODEL_PROVIDER, "openai")

        if hasattr(span_data, "input"):
            messages, tool_call_ids = _process_input_messages(span_data.input)

            for tool_id in tool_call_ids:
                tool_call = self.tool_tracker.find_by_id(tool_id)
                if not tool_call or not tool_call.tool_span_id or tool_call.is_handoff_completed:
                    continue

                if tool_call.tool_kind == "handoff":
                    self.tool_tracker.mark_handoff_completed(tool_id)

                add_span_link(
                    llmobs_span,
                    tool_call.tool_span_id,
                    LLMObs.export_span(llmobs_span)["trace_id"],
                    "output",
                    "input",
                )

            llmobs_span._set_ctx_item(INPUT_MESSAGES, messages)

        # Set output messages using Pydantic model parser
        if hasattr(span_data.response, "output"):
            messages, tool_call_ids = _process_output_messages(span_data.response.output)
            for tool_id, tool_name, tool_args in tool_call_ids:
                self.tool_tracker.register_llm_tool_call(
                    tool_id=tool_id,
                    tool_name=tool_name,
                    arguments=tool_args,
                    llm_span_id=LLMObs.export_span(llmobs_span)["span_id"],
                )

            llmobs_span._set_ctx_item(OUTPUT_MESSAGES, messages)

        # Build response metadata
        metadata = {}
        for field in [
            "temperature",
            "max_output_tokens",
            "top_p",
            "tools",
            "tool_choice",
            "truncation",
        ]:
            if hasattr(span_data.response, field):
                value = getattr(span_data.response, field)
                if value is not None:
                    metadata[field] = _load_value(value)

        # Add text metadata if present
        if hasattr(span_data.response, "text") and span_data.response.text:
            metadata["text"] = _load_value(span_data.response.text)

        # Build usage metrics
        metrics = {}
        if hasattr(span_data.response, "usage"):
            usage = span_data.response.usage
            if hasattr(usage, "input_tokens"):
                metrics["input_tokens"] = usage.input_tokens
            if hasattr(usage, "output_tokens"):
                metrics["output_tokens"] = usage.output_tokens
            if hasattr(usage, "total_tokens"):
                metrics["total_tokens"] = usage.total_tokens
            if hasattr(usage, "output_tokens_details"):
                metadata["reasoning_tokens"] = usage.output_tokens_details.reasoning_tokens

        if metadata:
            llmobs_span._set_ctx_item(METADATA, metadata)
        if metrics:
            llmobs_span._set_ctx_item(METRICS, metrics)

    def set_span_attributes_tool(self, span_data, llmobs_span) -> None:
        """Sets attributes for function/tool type spans."""
        llmobs_span._set_ctx_item(INPUT_VALUE, getattr(span_data, "input", ""))
        output = getattr(span_data, "output", "")
        llmobs_span._set_ctx_item(OUTPUT_VALUE, output)

        tool_call = self.tool_tracker.find_by_input(span_data.name, span_data.input)
        if tool_call and tool_call.llm_span_id:
            add_span_link(
                llmobs_span,
                tool_call.llm_span_id,
                LLMObs.export_span(llmobs_span)["trace_id"],
                "output",
                "input",
            )

            # Register this tool execution
            self.tool_tracker.register_tool_execution(
                tool_id=tool_call.tool_id,
                tool_span_id=LLMObs.export_span(llmobs_span)["span_id"],
                tool_kind="function",
            )
            # Cleanup the input lookup since we've matched it
            self.tool_tracker.cleanup_input(span_data.name, span_data.input)

        metadata = {}
        if hasattr(span_data, "function_name"):
            metadata["function_name"] = span_data.function_name
        if hasattr(span_data, "arguments"):
            metadata["arguments"] = span_data.arguments

        if metadata:
            llmobs_span._set_ctx_item(METADATA, metadata)

    def set_span_attributes_handoff(self, span_data, llmobs_span, span) -> None:
        """Sets attributes for handoff type spans."""
        handoff_tool_name = "transfer_to_{}".format("_".join(span_data.to_agent.split(" ")).lower())
        llmobs_span.name = handoff_tool_name

        tool_call = self.tool_tracker.find_by_input(handoff_tool_name, "{}")
        if tool_call and tool_call.llm_span_id:
            add_span_link(
                llmobs_span,
                tool_call.llm_span_id,
                LLMObs.export_span(llmobs_span)["trace_id"],
                "output",
                "input",
            )
            self.tool_tracker.register_tool_execution(
                tool_id=tool_call.tool_id,
                tool_span_id=LLMObs.export_span(llmobs_span)["span_id"],
                tool_kind="handoff",
            )

        llmobs_span._set_ctx_item(INPUT_VALUE, getattr(span_data, "from_agent", ""))
        llmobs_span._set_ctx_item(OUTPUT_VALUE, getattr(span_data, "to_agent", ""))

    def set_span_attributes_agent(self, span_data, llmobs_span, span) -> None:
        """Sets attributes for agent type spans."""
        metadata = {}
        if hasattr(span_data, "handoffs"):
            metadata["handoffs"] = _load_value(span_data.handoffs)
        if hasattr(span_data, "tools"):
            metadata["tools"] = _load_value(span_data.tools)

        if metadata:
            llmobs_span._set_ctx_item(METADATA, metadata)

    def set_span_attributes_generation(self, span_data, llmobs_span) -> None:
        """Sets attributes for generation type spans."""
        if hasattr(span_data, "model"):
            llmobs_span._set_ctx_item(MODEL_NAME, span_data.model)
            llmobs_span._set_ctx_item(MODEL_PROVIDER, "openai")

        # Set input and output messages
        if hasattr(span_data, "input"):
            llmobs_span._set_ctx_item(INPUT_MESSAGES, span_data.input)

        if hasattr(span_data, "output"):
            llmobs_span._set_ctx_item(OUTPUT_MESSAGES, span_data.output)

        # Set metadata from model_config
        metadata = {}
        if hasattr(span_data, "model_config") and span_data.model_config:
            metadata.update(_load_value(span_data.model_config))

        # Set usage metrics
        metrics = {}
        if hasattr(span_data, "usage") and span_data.usage:
            if "input_tokens" in span_data.usage:
                metrics["input_tokens"] = span_data.usage["input_tokens"]
            if "output_tokens" in span_data.usage:
                metrics["output_tokens"] = span_data.usage["output_tokens"]

        if metadata:
            llmobs_span._set_ctx_item(METADATA, metadata)
        if metrics:
            llmobs_span._set_ctx_item(METRICS, metrics)

    def set_span_attributes(self, span: OaiSpan[Any], llmobs_span: DdSpan) -> None:
        """Sets attributes directly on the llmobs span based on the span type and data.

        Args:
            span: The OpenAI span containing the data
            llmobs_span: The LLMObs span to set attributes on
        """
        if not span or not llmobs_span:
            return

        try:
            span_data = span.span_data
            if not span_data:
                return

            span_type = getattr(span_data, "type", None)

            set_error_on_span(llmobs_span, span)

            if span_type == "response":
                self.set_span_attributes_response(span_data, llmobs_span)
            elif span_type in ["function", "tool"]:
                self.set_span_attributes_tool(span_data, llmobs_span)
            elif span_type == "handoff":
                self.set_span_attributes_handoff(span_data, llmobs_span, span)
            elif span_type == "agent":
                self.set_span_attributes_agent(span_data, llmobs_span, span)
            elif span_type == "generation":
                self.set_span_attributes_generation(span_data, llmobs_span)
        except Exception as e:
            logger.warning("Error setting span attributes: %s", e)
            # Still finish the span even if we fail to set attributes
            llmobs_span.finish()


class NoOpTraceProcessor(TracingProcessor):
    def __init__(self):
        super().__init__()

    def on_trace_start(self, trace: OaiTrace) -> None:
        """Called when a trace is started.

        Args:
            trace: The trace that started.
        """
        pass

    def on_trace_end(self, trace: OaiTrace) -> None:
        """Called when a trace is finished.

        Args:
            trace: The trace that started.
        """
        pass

    def on_span_start(self, span: OaiSpan[Any]) -> None:
        """Called when a span is started.

        Args:
            span: The span that started.
        """
        pass

    def on_span_end(self, span: OaiSpan[Any]) -> None:
        """Called when a span is finished. Should not block or raise exceptions.

        Args:
            span: The span that finished.
        """
        pass

    def shutdown(self) -> None:
        """Called when the application stops."""
        pass

    def force_flush(self) -> None:
        """Forces an immediate flush of all queued spans/traces."""
        pass
