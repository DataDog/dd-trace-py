from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import is_dataclass
import json
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import TypeVar

from agents.tracing.spans import Span as OaiSpan
from agents.tracing.traces import Trace as OaiTrace

from ddtrace.internal.logger import get_logger


logger = get_logger(__name__)

T = TypeVar("T")


class OaiSpanAdapter:
    """Adapter for Oai Agents SDK Span objects that the llmobs integration code will use.
    This is so the integrations code does not need to interact directly with any oai agent sdk types.
    It is also handy for providing defaults when we bump into missing data or unexpected data shapes.
    """

    def __init__(self, span: OaiSpan[Any]):
        self._span = span

    @property
    def span_id(self) -> str:
        """Get the span ID."""
        return self._span.span_id

    @property
    def trace_id(self) -> str:
        """Get the trace ID."""
        return self._span.trace_id

    @property
    def name(self) -> str:
        """Get the span name."""
        if hasattr(self._span, "span_data") and hasattr(self._span.span_data, "name"):
            return self._span.span_data.name
        return "openai_agents.{}".format(self.span_type.lower())

    @property
    def span_type(self) -> str:
        """Get the span type."""
        if hasattr(self._span, "span_data") and hasattr(self._span.span_data, "type"):
            return self._span.span_data.type
        return ""

    @property
    def llmobs_span_kind(self) -> str:
        kind_mapping = {
            "function": "tool",
            "agent": "agent",
            "handoff": "tool",
            "response": "llm",
            "guardrail": "task",
            "custom": "task",
        }
        return kind_mapping.get(self.span_type, "task")

    @property
    def input(self) -> str | list[Any]:
        """Get the span data input."""
        if not hasattr(self._span, "span_data"):
            return ""
        return getattr(self._span.span_data, "input", "")

    @property
    def output(self) -> Optional[str]:
        """Get the span data output."""
        if not hasattr(self._span, "span_data"):
            return None
        return getattr(self._span.span_data, "output", None)

    @property
    def response(self) -> Optional[Any]:
        """Get the span data response."""
        if not hasattr(self._span, "span_data"):
            return None
        return getattr(self._span.span_data, "response", None)

    @property
    def from_agent(self) -> str:
        """Get the span data from_agent."""
        if not hasattr(self._span, "span_data"):
            return ""
        return getattr(self._span.span_data, "from_agent", "")

    @property
    def to_agent(self) -> str:
        """Get the span data to_agent."""
        if not hasattr(self._span, "span_data"):
            return ""
        return getattr(self._span.span_data, "to_agent", "")

    @property
    def handoffs(self) -> Optional[List[str]]:
        """Get the span data handoffs."""
        if not hasattr(self._span, "span_data"):
            return None
        return getattr(self._span.span_data, "handoffs", None)

    @property
    def tools(self) -> Optional[List[str]]:
        """Get the span data tools."""
        if not hasattr(self._span, "span_data"):
            return None
        return getattr(self._span.span_data, "tools", None)

    @property
    def data(self) -> Any:
        """Get the span data for custom spans."""
        if not hasattr(self._span, "span_data"):
            return None
        return getattr(self._span.span_data, "data", None)

    @property
    def formatted_custom_data(self) -> Dict[str, Any]:
        """Get the custom span data in a formatted way."""
        data = self.data
        if not data:
            return {}
        return load_span_data_value(data)

    @property
    def response_output_text(self) -> str:
        """Get the output text from the response."""
        if not self.response:
            return ""
        return self.response.output_text

    @property
    def llmobs_model_name(self) -> Optional[str]:
        """Get the model name formatted for LLMObs."""
        if not hasattr(self._span, "span_data"):
            return None

        if self.span_type == "response" and self.response:
            if hasattr(self.response, "model"):
                return self.response.model
        return None

    @property
    def llmobs_metrics(self) -> Optional[Dict[str, Any]]:
        """Get metrics from the span data formatted for LLMObs."""
        if not hasattr(self._span, "span_data"):
            return None

        metrics = {}

        if self.span_type == "response" and self.response and hasattr(self.response, "usage"):
            usage = self.response.usage
            if hasattr(usage, "input_tokens"):
                metrics["input_tokens"] = usage.input_tokens
            if hasattr(usage, "output_tokens"):
                metrics["output_tokens"] = usage.output_tokens
            if hasattr(usage, "total_tokens"):
                metrics["total_tokens"] = usage.total_tokens

        return metrics if metrics else None

    @property
    def llmobs_metadata(self) -> Optional[Dict[str, Any]]:
        """Get metadata from the span data formatted for LLMObs."""
        if not hasattr(self._span, "span_data"):
            return None

        metadata = {}

        if self.span_type == "response" and self.response:
            for field in ["temperature", "max_output_tokens", "top_p", "tools", "tool_choice", "truncation"]:
                if hasattr(self.response, field):
                    value = getattr(self.response, field)
                    if value is not None:
                        metadata[field] = load_span_data_value(value)

            if hasattr(self.response, "text") and self.response.text:
                metadata["text"] = load_span_data_value(self.response.text)

            if hasattr(self.response, "usage") and hasattr(self.response.usage, "output_tokens_details"):
                metadata["reasoning_tokens"] = self.response.usage.output_tokens_details.reasoning_tokens

        if self.span_type == "agent":
            agent_metadata = {
                "handoffs": [],  # Always include empty arrays
                "tools": [],  # Always include empty arrays
            }

            if self.handoffs:
                agent_metadata["handoffs"] = load_span_data_value(self.handoffs)
            if self.tools:
                agent_metadata["tools"] = load_span_data_value(self.tools)

            metadata.update(agent_metadata)

        if self.span_type == "custom" and hasattr(self._span.span_data, "data"):
            custom_data = getattr(self._span.span_data, "data", None)
            if custom_data:
                metadata.update(custom_data)

        return metadata if metadata else None

    @property
    def error(self) -> Optional[Dict[str, Any]]:
        """Get span error if it exists."""
        return self._span.error if hasattr(self._span, "error") else None

    def get_error_message(self) -> Optional[str]:
        """Get the error message if an error exists."""
        if not self.error:
            return None
        return self.error.get("message")

    def get_error_data(self) -> Optional[Dict[str, Any]]:
        """Get the error data if an error exists."""
        if not self.error:
            return None
        return self.error.get("data")

    def llmobs_input_messages(self) -> Tuple[List[Dict[str, Any]], List[str]]:
        """Returns processed input messages for LLM Obs LLM spans.

        Returns:
            A tuple of (processed_messages, tool_call_ids)
        """
        messages = self.input
        processed = []
        if not messages:
            return processed, []

        if isinstance(messages, str):
            return [{"content": messages, "role": "user"}], []

        tool_call_ids = []

        for item in messages:
            processed_item = {}
            # Handle regular message
            if "content" in item and "role" in item:
                processed_item["content"] = ""
                if isinstance(item["content"], list):
                    for content in item["content"]:
                        if content.get("text"):
                            processed_item["content"] += content.get("text")
                        elif content.get("refusal"):
                            processed_item["content"] += content.get("refusal")
                        else:
                            processed_item["content"] += str(content)
                else:
                    processed_item["content"] = item["content"]

                processed_item["role"] = item["role"]
            elif "call_id" in item and "arguments" in item:
                """
                Process `ResponseFunctionToolCallParam` type from input messages
                """
                try:
                    arguments = json.loads(item["arguments"])
                except json.JSONDecodeError:
                    arguments = item["arguments"]
                processed_item["tool_calls"] = [
                    {
                        "tool_id": item["call_id"],
                        "arguments": arguments,
                        "name": item.get("name", ""),
                        "type": item.get("type", "function_call"),
                    }
                ]
            elif "call_id" in item and "output" in item:
                """
                Process `FunctionCallOutput` type from input messages
                """
                output = item["output"]

                # a tool call id was used as input
                tool_call_ids.append(item["call_id"])
                if isinstance(output, str):
                    try:
                        output = json.loads(output)
                    except json.JSONDecodeError:
                        output = {"output": output}
                processed_item["tool_calls"] = [
                    {
                        "tool_id": item["call_id"],
                        "type": item.get("type", "function_call_output"),
                    }
                ]
            if processed_item:
                processed.append(processed_item)

        return processed, tool_call_ids

    def llmobs_output_messages(self) -> Tuple[List[Dict[str, Any]], List[Tuple[str, str, str]]]:
        """Returns processed output messages for LLM Obs LLM spans.

        Returns:
            A tuple of (processed_messages, tool_call_outputs)
        """
        if not self.response or not self.response.output:
            return [], []

        messages = self.response.output
        processed = []
        if not messages:
            return processed, []

        if not isinstance(messages, list):
            messages = [messages]

        tool_call_ids = []
        for item in messages:
            message = {}
            # Handle content-based messages
            if hasattr(item, "content"):
                text = ""
                for content in item.content:
                    if hasattr(content, "text"):
                        text += content.text
                    elif hasattr(content, "refusal"):
                        text += content.refusal
                    else:
                        text += str(content)
                message.update({"role": getattr(item, "role", "assistant"), "content": text})
            # Handle tool calls
            elif hasattr(item, "call_id") and hasattr(item, "arguments"):
                tool_call_ids.append((item.call_id, item.name, item.arguments if item.arguments else "{}"))
                message.update(
                    {
                        "tool_calls": [
                            {
                                "tool_id": item.call_id,
                                "arguments": json.loads(item.arguments)
                                if isinstance(item.arguments, str)
                                else item.arguments,
                                "name": getattr(item, "name", ""),
                                "type": getattr(item, "type", "function"),
                            }
                        ]
                    }
                )
            else:
                message.update({"content": str(item)})
            processed.append(message)

        return processed, tool_call_ids

    def llmobs_trace_input(self) -> Optional[str]:
        """Converts Response span data to an input value for top level trace.

        Returns:
            The input content if found, None otherwise.
        """
        if not self.response or not self.input:
            return None

        try:
            messages, _ = self.llmobs_input_messages()
            if messages and len(messages) > 0:
                return messages[0].get("content")
        except (AttributeError, IndexError):
            from ddtrace.internal.logger import get_logger

            logger = get_logger(__name__)
            logger.warning("Failed to process input messages from `response` span", exc_info=True)
        return None


class OaiTraceAdapter:
    """Adapter for OpenAI Agents SDK Trace objects.

    This class provides a clean interface for the integration code to interact with
    OpenAI Agents SDK Trace objects without needing to know their internal structure.
    """

    def __init__(self, trace: OaiTrace):
        self._trace = trace

    @property
    def trace_id(self) -> str:
        """Get the trace ID."""
        return self._trace.trace_id

    @property
    def name(self) -> str:
        """Get the trace name."""
        return getattr(self._trace, "name", "Agent workflow")

    @property
    def group_id(self) -> Optional[str]:
        """Get the group ID if it exists."""
        try:
            return self._trace.export().get("group_id")
        except (AttributeError, KeyError):
            return None

    @property
    def metadata(self) -> Optional[Dict[str, Any]]:
        """Get the trace metadata if it exists."""
        return self._trace.metadata if hasattr(self._trace, "metadata") else None

    @property
    def raw_trace(self) -> OaiTrace:
        """Get the raw OpenAI Agents SDK trace."""
        return self._trace


def load_span_data_value(value):
    """Helper function to load values stored in span data in a consistent way"""
    if isinstance(value, list):
        return [load_span_data_value(item) for item in value]
    elif hasattr(value, "model_dump"):
        return value.model_dump()
    elif is_dataclass(value):
        return asdict(value)
    else:
        return json.loads(json.dumps(value))


@dataclass
class LLMObsTraceInfo:
    """Metadata for llmobs trace used for setting root span attributes and span links"""

    trace_id: str
    span_id: str
    """
    We only update trace's input/output llm spans when that llm span's parent is a top-level agent span.
    This is to ignore extraneous nested llm spans that aren't part of the core agent logic.
    """
    current_top_level_agent_span_id: Optional[str] = None
    input_oai_span: Optional[OaiSpanAdapter] = None
    output_oai_span: Optional[OaiSpanAdapter] = None


@dataclass
class ToolCall:
    """Tool call and its associated llmobs spans."""

    tool_id: str
    tool_name: str
    arguments: str
    llm_span_id: Optional[str] = None  # ID of the LLM span that initiated this tool call
    tool_span_id: Optional[str] = None  # ID of the tool span that executed this call
    tool_kind: Optional[str] = None  # one of "function", "handoff"
    is_handoff_completed: bool = False  # Track if handoff is completed to noisy links


class ToolCallTracker:
    """Used to track tool data and their associated llm/tool spans for span linking."""

    def __init__(self):
        self._tool_calls: Dict[str, ToolCall] = {}  # tool_id -> ToolCall
        self._input_lookup: Dict[Tuple[str, str], str] = {}  # (name, args) -> tool_id

    def register_llm_tool_call(self, tool_id: str, tool_name: str, arguments: str, llm_span_id: str) -> None:
        tool_call = ToolCall(
            tool_id=tool_id,
            tool_name=tool_name,
            arguments=arguments,
            llm_span_id=llm_span_id,
        )
        self._tool_calls[tool_id] = tool_call
        self._input_lookup[(tool_name, arguments)] = tool_id

    def register_tool_execution(self, tool_id: str, tool_span_id: str, tool_kind: str) -> None:
        if tool_id in self._tool_calls:
            self._tool_calls[tool_id].tool_span_id = tool_span_id
            self._tool_calls[tool_id].tool_kind = tool_kind

    def mark_handoff_completed(self, tool_id: str) -> None:
        # we need to mark when a hand-off is completed since we only want to link the output of a
        # handoff tool span to the FIRST llm call that has that hand-off as input.
        if tool_id in self._tool_calls:
            self._tool_calls[tool_id].is_handoff_completed = True

    def find_by_input(self, tool_name: str, arguments: str) -> Optional[ToolCall]:
        tool_id = self._input_lookup.get((tool_name, arguments))
        return self._tool_calls.get(tool_id) if tool_id else None

    def find_by_id(self, tool_id: str) -> Optional[ToolCall]:
        return self._tool_calls.get(tool_id)

    def cleanup_input(self, tool_name: str, arguments: str) -> None:
        self._input_lookup.pop((tool_name, arguments), None)
