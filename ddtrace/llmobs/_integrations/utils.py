from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import is_dataclass
import json
import re
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union
from urllib.parse import urlparse

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import OAI_HANDOFF_TOOL_ARG
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._utils import _get_attr
from ddtrace.llmobs._utils import safe_json


logger = get_logger(__name__)

ACCEPTED_OPENAI_DEFAULT_HOSTNAMES = ("api.openai.com", "api.deepseek.com")
AZURE_URL_REGEX_PATTERN = "^[\\w.-]*openai\\.azure\\.com$"


def is_openai_default_base_url(base_url: Optional[str] = None) -> bool:
    if base_url is None:
        return True

    parsed_url = urlparse(base_url)
    default_azure_endpoint_regex = re.compile(AZURE_URL_REGEX_PATTERN)
    matches_azure_endpoint = default_azure_endpoint_regex.match(parsed_url.hostname or "") is not None
    return parsed_url.hostname in ACCEPTED_OPENAI_DEFAULT_HOSTNAMES or matches_azure_endpoint


def extract_model_name_google(instance, model_name_attr):
    """Extract the model name from the instance.
    Model names are stored in the format `"models/{model_name}"`
    so we do our best to return the model name instead of the full string.
    """
    model_name = _get_attr(instance, model_name_attr, "")
    if not model_name or not isinstance(model_name, str):
        return ""
    if "/" in model_name:
        return model_name.split("/")[-1]
    return model_name


def get_generation_config_google(instance, kwargs):
    """
    The generation config can be defined on the model instance or
    as a kwarg of the request. Therefore, try to extract this information
    from the kwargs and otherwise default to checking the model instance attribute.
    """
    generation_config = kwargs.get("generation_config", {})
    return generation_config or _get_attr(instance, "_generation_config", {})


def tag_request_content_part_google(tag_prefix, span, integration, part, part_idx, content_idx):
    """Tag the generation span with request content parts."""
    text = _get_attr(part, "text", "")
    function_call = _get_attr(part, "function_call", None)
    function_response = _get_attr(part, "function_response", None)
    span.set_tag_str(
        "%s.request.contents.%d.parts.%d.text" % (tag_prefix, content_idx, part_idx), integration.trunc(str(text))
    )
    if function_call:
        function_call_dict = type(function_call).to_dict(function_call)
        span.set_tag_str(
            "%s.request.contents.%d.parts.%d.function_call.name" % (tag_prefix, content_idx, part_idx),
            function_call_dict.get("name", ""),
        )
        span.set_tag_str(
            "%s.request.contents.%d.parts.%d.function_call.args" % (tag_prefix, content_idx, part_idx),
            integration.trunc(str(function_call_dict.get("args", {}))),
        )
    if function_response:
        function_response_dict = type(function_response).to_dict(function_response)
        span.set_tag_str(
            "%s.request.contents.%d.parts.%d.function_response.name" % (tag_prefix, content_idx, part_idx),
            function_response_dict.get("name", ""),
        )
        span.set_tag_str(
            "%s.request.contents.%d.parts.%d.function_response.response" % (tag_prefix, content_idx, part_idx),
            integration.trunc(str(function_response_dict.get("response", {}))),
        )


def tag_response_part_google(tag_prefix, span, integration, part, part_idx, candidate_idx):
    """Tag the generation span with response part text and function calls."""
    text = _get_attr(part, "text", "")
    span.set_tag_str(
        "%s.response.candidates.%d.content.parts.%d.text" % (tag_prefix, candidate_idx, part_idx),
        integration.trunc(str(text)),
    )
    function_call = _get_attr(part, "function_call", None)
    if not function_call:
        return
    span.set_tag_str(
        "%s.response.candidates.%d.content.parts.%d.function_call.name" % (tag_prefix, candidate_idx, part_idx),
        _get_attr(function_call, "name", ""),
    )
    span.set_tag_str(
        "%s.response.candidates.%d.content.parts.%d.function_call.args" % (tag_prefix, candidate_idx, part_idx),
        integration.trunc(str(_get_attr(function_call, "args", {}))),
    )


def llmobs_get_metadata_google(kwargs, instance):
    metadata = {}
    model_config = getattr(instance, "_generation_config", {}) or {}
    model_config = model_config.to_dict() if hasattr(model_config, "to_dict") else model_config
    request_config = kwargs.get("generation_config", {}) or {}
    request_config = request_config.to_dict() if hasattr(request_config, "to_dict") else request_config

    parameters = ("temperature", "max_output_tokens", "candidate_count", "top_p", "top_k")
    for param in parameters:
        model_config_value = _get_attr(model_config, param, None)
        request_config_value = _get_attr(request_config, param, None)
        if model_config_value or request_config_value:
            metadata[param] = request_config_value or model_config_value
    return metadata


def extract_message_from_part_google(part, role=None):
    text = _get_attr(part, "text", "")
    function_call = _get_attr(part, "function_call", None)
    function_response = _get_attr(part, "function_response", None)
    message = {"content": text}
    if role:
        message["role"] = role
    if function_call:
        function_call_dict = function_call
        if not isinstance(function_call, dict):
            function_call_dict = type(function_call).to_dict(function_call)
        message["tool_calls"] = [
            {"name": function_call_dict.get("name", ""), "arguments": function_call_dict.get("args", {})}
        ]
    if function_response:
        function_response_dict = function_response
        if not isinstance(function_response, dict):
            function_response_dict = type(function_response).to_dict(function_response)
        message["content"] = "[tool result: {}]".format(function_response_dict.get("response", ""))
    return message


def get_llmobs_metrics_tags(integration_name, span):
    usage = {}

    # check for both prompt / completion or input / output tokens
    input_tokens = span.get_metric("%s.response.usage.prompt_tokens" % integration_name) or span.get_metric(
        "%s.response.usage.input_tokens" % integration_name
    )
    output_tokens = span.get_metric("%s.response.usage.completion_tokens" % integration_name) or span.get_metric(
        "%s.response.usage.output_tokens" % integration_name
    )
    total_tokens = None
    if input_tokens and output_tokens:
        total_tokens = input_tokens + output_tokens
    total_tokens = span.get_metric("%s.response.usage.total_tokens" % integration_name) or total_tokens

    if input_tokens is not None:
        usage[INPUT_TOKENS_METRIC_KEY] = input_tokens
    if output_tokens is not None:
        usage[OUTPUT_TOKENS_METRIC_KEY] = output_tokens
    if total_tokens is not None:
        usage[TOTAL_TOKENS_METRIC_KEY] = total_tokens
    return usage


def get_system_instructions_from_google_model(model_instance):
    """
    Extract system instructions from model and convert to []str for tagging.
    """
    try:
        from google.ai.generativelanguage_v1beta.types.content import Content
    except ImportError:
        Content = None
    try:
        from vertexai.generative_models._generative_models import Part
    except ImportError:
        Part = None

    raw_system_instructions = getattr(model_instance, "_system_instruction", [])
    if Content is not None and isinstance(raw_system_instructions, Content):
        system_instructions = []
        for part in raw_system_instructions.parts:
            system_instructions.append(_get_attr(part, "text", ""))
        return system_instructions
    elif isinstance(raw_system_instructions, str):
        return [raw_system_instructions]
    elif Part is not None and isinstance(raw_system_instructions, Part):
        return [_get_attr(raw_system_instructions, "text", "")]
    elif not isinstance(raw_system_instructions, list):
        return []

    system_instructions = []
    for elem in raw_system_instructions:
        if isinstance(elem, str):
            system_instructions.append(elem)
        elif Part is not None and isinstance(elem, Part):
            system_instructions.append(_get_attr(elem, "text", ""))
    return system_instructions


LANGCHAIN_ROLE_MAPPING = {
    "human": "user",
    "ai": "assistant",
    "system": "system",
}


def format_langchain_io(
    messages,
):
    """
    Formats input and output messages for serialization to JSON.
    Specifically, makes sure that any schema messages are converted to strings appropriately.
    """
    if isinstance(messages, dict):
        formatted = {}
        for key, value in messages.items():
            formatted[key] = format_langchain_io(value)
        return formatted
    if isinstance(messages, list):
        return [format_langchain_io(message) for message in messages]
    return get_content_from_langchain_message(messages)


def get_content_from_langchain_message(message) -> Union[str, Tuple[str, str]]:
    """
    Attempts to extract the content and role from a message (AIMessage, HumanMessage, SystemMessage) object.
    """
    if isinstance(message, str):
        return message
    try:
        content = getattr(message, "__dict__", {}).get("content", str(message))
        role = getattr(message, "role", LANGCHAIN_ROLE_MAPPING.get(getattr(message, "type"), ""))
        return (role, content) if role else content
    except AttributeError:
        return str(message)


def get_messages_from_converse_content(role: str, content: list):
    """
    Extracts out a list of messages from a converse `content` field.

    `content` is a list of `ContentBlock` objects. Each `ContentBlock` object is a union type
    of `text`, `toolUse`, amd more content types. We only support extracting out `text` and `toolUse`.

    For more info, see `ContentBlock` spec
    https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_ContentBlock.html
    """
    if not content or not isinstance(content, list) or not isinstance(content[0], dict):
        return []
    messages = []  # type: list[dict[str, Union[str, list[dict[str, dict]]]]]
    content_blocks = []
    tool_calls_info = []
    for content_block in content:
        if content_block.get("text") and isinstance(content_block.get("text"), str):
            content_blocks.append(content_block.get("text", ""))
        elif content_block.get("toolUse") and isinstance(content_block.get("toolUse"), dict):
            toolUse = content_block.get("toolUse", {})
            tool_calls_info.append(
                {
                    "name": str(toolUse.get("name", "")),
                    "arguments": toolUse.get("input", {}),
                    "tool_id": str(toolUse.get("toolUseId", "")),
                }
            )
        else:
            content_type = ",".join(content_block.keys())
            messages.append({"content": "[Unsupported content type: {}]".format(content_type), "role": role})
    message = {}  # type: dict[str, Union[str, list[dict[str, dict]]]]
    if tool_calls_info:
        message.update({"tool_calls": tool_calls_info})
    if content_blocks:
        message.update({"content": " ".join(content_blocks), "role": role})
    if message:
        messages.append(message)
    return messages


class OaiSpanAdapter:
    """Adapter for Oai Agents SDK Span objects that the llmobs integration code will use.
    This is to consolidate the code where we access oai library types which provides a clear starting point for
    troubleshooting data issues.
    It is also handy for providing defaults when we bump into missing data or unexpected data shapes.
    """

    def __init__(self, oai_span):
        self._raw_oai_span = oai_span  # openai span data type

    @property
    def span_id(self) -> str:
        """Get the span ID."""
        return self._raw_oai_span.span_id

    @property
    def trace_id(self) -> str:
        """Get the trace ID."""
        return self._raw_oai_span.trace_id

    @property
    def name(self) -> str:
        """Get the span name."""
        if hasattr(self._raw_oai_span, "span_data") and hasattr(self._raw_oai_span.span_data, "name"):
            return self._raw_oai_span.span_data.name
        return "openai_agents.{}".format(self.span_type.lower())

    @property
    def span_type(self) -> str:
        """Get the span type."""
        if hasattr(self._raw_oai_span, "span_data") and hasattr(self._raw_oai_span.span_data, "type"):
            return self._raw_oai_span.span_data.type
        return ""

    @property
    def llmobs_span_kind(self) -> Optional[str]:
        kind_mapping = {
            "function": "tool",
            "agent": "agent",
            "handoff": "tool",
            "response": "llm",
            "guardrail": "task",
            "custom": "task",
        }
        return kind_mapping.get(self.span_type)

    @property
    def input(self) -> Union[str, List[Any]]:
        """Get the span data input."""
        if not hasattr(self._raw_oai_span, "span_data"):
            return ""
        return getattr(self._raw_oai_span.span_data, "input", "")

    @property
    def output(self) -> Optional[str]:
        """Get the span data output."""
        if not hasattr(self._raw_oai_span, "span_data"):
            return None
        return getattr(self._raw_oai_span.span_data, "output", None)

    @property
    def response(self) -> Optional[Any]:
        """Get the span data response."""
        if not hasattr(self._raw_oai_span, "span_data"):
            return None
        return getattr(self._raw_oai_span.span_data, "response", None)

    @property
    def from_agent(self) -> str:
        """Get the span data from_agent."""
        if not hasattr(self._raw_oai_span, "span_data"):
            return ""
        return getattr(self._raw_oai_span.span_data, "from_agent", "")

    @property
    def to_agent(self) -> str:
        """Get the span data to_agent."""
        if not hasattr(self._raw_oai_span, "span_data"):
            return ""
        return getattr(self._raw_oai_span.span_data, "to_agent", "")

    @property
    def handoffs(self) -> Optional[List[str]]:
        """Get the span data handoffs."""
        if not hasattr(self._raw_oai_span, "span_data"):
            return None
        return getattr(self._raw_oai_span.span_data, "handoffs", None)

    @property
    def tools(self) -> Optional[List[str]]:
        """Get the span data tools."""
        if not hasattr(self._raw_oai_span, "span_data"):
            return None
        return getattr(self._raw_oai_span.span_data, "tools", None)

    @property
    def data(self) -> Any:
        """Get the span data for custom spans."""
        if not hasattr(self._raw_oai_span, "span_data"):
            return None
        return getattr(self._raw_oai_span.span_data, "data", None)

    @property
    def formatted_custom_data(self) -> Dict[str, Any]:
        """Get the custom span data in a formatted way."""
        data = self.data
        if not data:
            return {}
        return load_oai_span_data_value(data)

    @property
    def response_output_text(self) -> str:
        """Get the output text from the response."""
        if not self.response:
            return ""
        return self.response.output_text

    @property
    def llmobs_model_name(self) -> Optional[str]:
        """Get the model name formatted for LLMObs."""
        if not hasattr(self._raw_oai_span, "span_data"):
            return None

        if self.span_type == "response" and self.response:
            if hasattr(self.response, "model"):
                return self.response.model
        return None

    @property
    def llmobs_metrics(self) -> Optional[Dict[str, Any]]:
        """Get metrics from the span data formatted for LLMObs."""
        if not hasattr(self._raw_oai_span, "span_data"):
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
        if not hasattr(self._raw_oai_span, "span_data"):
            return None

        metadata = {}

        if self.span_type == "response" and self.response:
            for field in ["temperature", "max_output_tokens", "top_p", "tools", "tool_choice", "truncation"]:
                if hasattr(self.response, field):
                    value = getattr(self.response, field)
                    if value is not None:
                        metadata[field] = load_oai_span_data_value(value)

            if hasattr(self.response, "text") and self.response.text:
                metadata["text"] = load_oai_span_data_value(self.response.text)

            if hasattr(self.response, "usage") and hasattr(self.response.usage, "output_tokens_details"):
                metadata["reasoning_tokens"] = self.response.usage.output_tokens_details.reasoning_tokens

        if self.span_type == "agent":
            agent_metadata: Dict[str, List[str]] = {
                "handoffs": [],
                "tools": [],
            }
            if self.handoffs:
                agent_metadata["handoffs"] = load_oai_span_data_value(self.handoffs)
            if self.tools:
                agent_metadata["tools"] = load_oai_span_data_value(self.tools)
            metadata.update(agent_metadata)

        if self.span_type == "custom" and hasattr(self._raw_oai_span.span_data, "data"):
            custom_data = getattr(self._raw_oai_span.span_data, "data", None)
            if custom_data:
                metadata.update(custom_data)

        return metadata if metadata else None

    @property
    def error(self) -> Optional[Dict[str, Any]]:
        """Get span error if it exists."""
        return self._raw_oai_span.error if hasattr(self._raw_oai_span, "error") else None

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
            - A list of processed messages
            - A list of tool call IDs for span linking purposes
        """
        messages = self.input
        processed: List[Dict[str, Any]] = []
        tool_call_ids: List[str] = []

        if not messages:
            return processed, tool_call_ids

        if isinstance(messages, str):
            return [{"content": messages, "role": "user"}], tool_call_ids

        for item in messages:
            processed_item: Dict[str, Union[str, List[Dict[str, str]]]] = {}
            # Handle regular message
            if "content" in item and "role" in item:
                processed_item_content = ""
                if isinstance(item["content"], list):
                    for content in item["content"]:
                        processed_item_content += content.get("text", "")
                        processed_item_content += content.get("refusal", "")
                else:
                    processed_item_content = item["content"]
                if processed_item_content:
                    processed_item["content"] = processed_item_content
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

                if isinstance(output, str):
                    try:
                        output = json.loads(output)
                    except json.JSONDecodeError:
                        output = {"output": output}
                tool_call_ids.append(item["call_id"])
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
            - A list of processed messages
            - A list of tool call data (name, id, args) for span linking purposes
        """
        if not self.response or not self.response.output:
            return [], []

        messages: List[Any] = self.response.output
        processed: List[Dict[str, Any]] = []
        tool_call_outputs: List[Tuple[str, str, str]] = []
        if not messages:
            return processed, tool_call_outputs

        if not isinstance(messages, list):
            messages = [messages]

        for item in messages:
            message = {}
            # Handle content-based messages
            if hasattr(item, "content"):
                text = ""
                for content in item.content:
                    if hasattr(content, "text") or hasattr(content, "refusal"):
                        text += getattr(content, "text", "")
                        text += getattr(content, "refusal", "")
                message.update({"role": getattr(item, "role", "assistant"), "content": text})
            # Handle tool calls
            elif hasattr(item, "call_id") and hasattr(item, "arguments"):
                tool_call_outputs.append(
                    (
                        item.call_id,
                        getattr(item, "name", ""),
                        item.arguments if item.arguments else OAI_HANDOFF_TOOL_ARG,
                    )
                )
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

        return processed, tool_call_outputs

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
                return messages[-1].get("content")
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

    def __init__(self, oai_trace):
        self._trace = oai_trace  # openai trace data type

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
    def raw_trace(self):
        """Get the raw OpenAI Agents SDK trace."""
        return self._trace


def load_oai_span_data_value(value):
    """Helper function to load values stored in openai span data in a consistent way"""
    if isinstance(value, list):
        return [load_oai_span_data_value(item) for item in value]
    elif hasattr(value, "model_dump"):
        return value.model_dump()
    elif is_dataclass(value):
        return asdict(value)
    else:
        value_str = safe_json(value)
        try:
            return json.loads(value_str)
        except json.JSONDecodeError:
            return value_str


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


def get_final_message_converse_stream_message(
    message: Dict[str, Any], text_blocks: Dict[int, str], tool_blocks: Dict[int, Dict[str, Any]]
) -> Dict[str, Any]:
    """Process a message and its content blocks into LLM Obs message format.

    Args:
        message: A message to be processed containing role and content block indices
        text_blocks: Mapping of content block indices to their text content
        tool_blocks: Mapping of content block indices to their tool usage data

    Returns:
        Dict containing the processed message with content and optional tool calls
    """
    indices = sorted(message.get("context_block_indices", []))
    message_output = {"role": message["role"]}

    text_contents = [text_blocks[idx] for idx in indices if idx in text_blocks]
    message_output.update({"content": "".join(text_contents)} if text_contents else {})

    tool_calls = []
    for idx in indices:
        tool_block = tool_blocks.get(idx)
        if not tool_block:
            continue
        tool_call = {
            "name": tool_block.get("toolName", ""),
            "tool_id": tool_block.get("toolUseId", ""),
        }
        tool_input = tool_block.get("input")
        if tool_input is not None:
            tool_args = {}
            try:
                tool_args = json.loads(tool_input)
            except (json.JSONDecodeError, ValueError):
                tool_args = {"input": tool_input}
            tool_call.update({"arguments": tool_args} if tool_args else {})
        tool_calls.append(tool_call)

    if tool_calls:
        message_output["tool_calls"] = tool_calls

    return message_output
