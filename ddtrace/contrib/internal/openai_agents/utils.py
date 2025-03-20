from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import is_dataclass
import json
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from agents.tracing.spans import Span as OaiSpan

from ddtrace._trace.span import Span as DdSpan
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs import LLMObs


logger = get_logger(__name__)


@dataclass
class LLMObsTraceInfo:
    """Metadata for llmobs trace used for setting root span attributes and span links"""

    trace_id: str
    span_id: str
    current_top_level_agent_span_id: Optional[str] = None
    input_oai_span: Optional[Any] = None
    output_oai_span: Optional[Any] = None


@dataclass
class ToolCall:
    """Tool call and its associated llmobs spans."""

    tool_id: str
    tool_name: str
    arguments: str
    llm_span_id: Optional[str] = None  # ID of the LLM span that initiated this tool call
    tool_span_id: Optional[str] = None  # ID of the tool span that executed this call
    tool_kind: Optional[str] = None  # Type of tool (function, handoff, etc)
    is_handoff_completed: bool = False  # Track if handoff is completed to avoid dupe links


class ToolCallTracker:
    """Used to track tool data and their associated llm/tool spans for span linking."""

    def __init__(self):
        self._tool_calls: Dict[str, ToolCall] = {}  # tool_id -> ToolCall
        self._input_lookup: Dict[Tuple[str, str], str] = {}  # (name, args) -> tool_id

    def register_llm_tool_call(self, tool_id: str, tool_name: str, arguments: str, llm_span_id: str) -> None:
        """Register a new tool call from an LLM."""
        tool_call = ToolCall(
            tool_id=tool_id,
            tool_name=tool_name,
            arguments=arguments,
            llm_span_id=llm_span_id,
        )
        self._tool_calls[tool_id] = tool_call
        self._input_lookup[(tool_name, arguments)] = tool_id

    def register_tool_execution(self, tool_id: str, tool_span_id: str, tool_kind: str) -> None:
        """Register the execution of a tool."""
        if tool_id in self._tool_calls:
            self._tool_calls[tool_id].tool_span_id = tool_span_id
            self._tool_calls[tool_id].tool_kind = tool_kind

    def mark_handoff_completed(self, tool_id: str) -> None:
        """Mark a handoff as completed."""
        if tool_id in self._tool_calls:
            self._tool_calls[tool_id].is_handoff_completed = True

    def find_by_input(self, tool_name: str, arguments: str) -> Optional[ToolCall]:
        """Find a tool call by its input parameters."""
        tool_id = self._input_lookup.get((tool_name, arguments))
        return self._tool_calls.get(tool_id) if tool_id else None

    def find_by_id(self, tool_id: str) -> Optional[ToolCall]:
        """Find a tool call by its ID."""
        return self._tool_calls.get(tool_id)

    def cleanup_input(self, tool_name: str, arguments: str) -> None:
        """Remove an input lookup entry after it's been used."""
        self._input_lookup.pop((tool_name, arguments), None)


def _determine_span_kind(span_type: str) -> str:
    """Maps OpenAI span types to Datadog LLMObs span kinds."""
    kind_mapping = {
        "generation": "llm",
        "function": "tool",
        "agent": "agent",
        "handoff": "tool",
        "response": "llm",
        "guardrail": "task",
        "custom": "task",
    }
    kind = kind_mapping.get(span_type, "task")
    return kind


def get_span_kind_from_span(span: OaiSpan) -> str:
    span_type = span.export().get("span_data", {}).get("type")
    return _determine_span_kind(span_type)


def start_span_fn(span_kind: str) -> callable:
    if span_kind == "llm":
        return LLMObs.llm
    elif span_kind == "tool":
        return LLMObs.tool
    elif span_kind == "agent":
        return LLMObs.agent
    elif span_kind == "workflow":
        return LLMObs.workflow
    else:
        return LLMObs.task


def set_error_on_span(llmobs_span: DdSpan, oai_span: OaiSpan) -> None:
    err = oai_span.error
    if err:
        llmobs_span.error = 1
        error_msg = err.get("message")
        data = err.get("data")
        llmobs_span.set_tag("error.message", error_msg + "\n" + json.dumps(data))


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


def _process_input_messages(messages: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Process input messages that are TypedDicts.
    Only handles Message and FunctionCallOutput types.
    """
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
            print("FUNCTION CALL INPUT: ", item)
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


def _process_output_messages(messages) -> List[Dict[str, Any]]:
    """Process output messages that are Pydantic models."""
    processed = []
    if not messages:
        return processed

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


def trace_input_from_response_span(response_span: OaiSpan[Any]) -> Optional[str]:
    """Get trace input from a response span safely.

    Args:
        response_span: The response span to get input from.

    Returns:
        The input content if found, None otherwise.
    """
    try:
        messages, _ = _process_input_messages(response_span.span_data.input)
        if messages and len(messages) > 0:
            return messages[0].get("content")
    except (AttributeError, IndexError):
        logger.warning("Failed to process input messages from `response` span", exc_info=True)
    return None
