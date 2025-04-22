import re
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from urllib.parse import urlparse

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._integrations import BaseLLMIntegration
from ddtrace.llmobs._integrations.utils import get_final_message_converse_stream_message
from ddtrace.llmobs._integrations.utils import get_messages_from_converse_content
from ddtrace.trace import Span


log = get_logger(__name__)

BEDROCK_URL_REGEX_PATTERN = "^bedrock-runtime[\\w.-]*.com$"


class BedrockIntegration(BaseLLMIntegration):
    _integration_name = "bedrock"

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        """Extract prompt/response attributes from an execution context.

        ctx is a required argument of the shape:
        {
            "resource": str, # oneof("Converse", "ConverseStream", "InvokeModel")
            "model_name": str,
            "model_provider": str,
            "llmobs.request_params": {"prompt": str | list[dict],
                                "temperature": Optional[float],
                                "max_tokens": Optional[int]
                                "top_p": Optional[int]}
            "llmobs.usage": Optional[dict],
            "llmobs.stop_reason": Optional[str],
        }
        """
        metadata = {}
        usage_metrics = {}
        ctx = args[0]

        request_params = ctx.get_item("llmobs.request_params") or {}

        if ctx.get_item("llmobs.stop_reason"):
            metadata["stop_reason"] = ctx["llmobs.stop_reason"]
        if ctx.get_item("llmobs.usage"):
            usage_metrics = ctx["llmobs.usage"]

        if "total_tokens" not in usage_metrics and (
            "input_tokens" in usage_metrics or "output_tokens" in usage_metrics
        ):
            usage_metrics["total_tokens"] = usage_metrics.get("input_tokens", 0) + usage_metrics.get("output_tokens", 0)

        if "temperature" in request_params and request_params.get("temperature") != "":
            metadata["temperature"] = float(request_params.get("temperature") or 0.0)
        if "max_tokens" in request_params and request_params.get("max_tokens") != "":
            metadata["max_tokens"] = int(request_params.get("max_tokens") or 0)

        prompt = request_params.get("prompt", "")

        is_converse = ctx["resource"] in ("Converse", "ConverseStream")
        input_messages = (
            self._extract_input_message_for_converse(prompt) if is_converse else self._extract_input_message(prompt)
        )

        output_messages = [{"content": ""}]
        if not span.error and response is not None:
            if ctx["resource"] == "Converse":
                output_messages = self._extract_output_message_for_converse(response)
            elif ctx["resource"] == "ConverseStream":
                (
                    output_messages,
                    additional_metadata,
                    streamed_usage_metrics,
                ) = self._extract_output_message_for_converse_stream(response)
                metadata.update(additional_metadata)
                usage_metrics.update(streamed_usage_metrics)
            else:
                output_messages = self._extract_output_message(response)

        span._set_ctx_items(
            {
                SPAN_KIND: "llm",
                MODEL_NAME: ctx.get_item("model_name") or "",
                MODEL_PROVIDER: ctx.get_item("model_provider") or "",
                INPUT_MESSAGES: input_messages,
                METADATA: metadata,
                METRICS: usage_metrics,
                OUTPUT_MESSAGES: output_messages,
            }
        )

    @staticmethod
    def _extract_input_message_for_converse(prompt: List[Dict[str, Any]]):
        """Extract input messages from the stored prompt for converse

        `prompt` is an array of `message` objects. Each `message` has a role and content field.

        The content field stores a list of `ContentBlock` objects.

        For more info, see bedrock converse request syntax:
        https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html#API_runtime_Converse_RequestSyntax
        """
        if not isinstance(prompt, list):
            log.warning("Bedrock input is not a list of messages or a string.")
            return [{"content": ""}]
        input_messages = []
        for message in prompt:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", ""))
            content = message.get("content", None)
            if not content or not isinstance(content, list):
                continue
            input_messages += get_messages_from_converse_content(role, content)
        return input_messages

    @staticmethod
    def _extract_output_message_for_converse(response: Dict[str, Any]):
        """Extract output messages from the stored prompt for converse

        `response` contains an `output` field that stores a nested `message` field.

        `message` has a `content` field that `ContentBlock` objects.

        For more info, see bedrock converse response syntax:
        https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html#API_runtime_Converse_ResponseSyntax
        """
        default_content = [{"content": ""}]
        message = response.get("output", {}).get("message", {})
        if not message:
            return default_content
        role = message.get("role", "assistant")
        content = message.get("content", None)
        if not content or not isinstance(content, list):
            return default_content
        return get_messages_from_converse_content(role, content)

    @staticmethod
    def _extract_output_message_for_converse_stream(
        streamed_body: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], Dict[str, str], Dict[str, int]]:
        """
        Extract output messages from streamed converse responses.

        Converse stream response comes in chunks. The chunks we care about are:
        - a message start/stop event, or
        - a content block start/stop event (for tool calls only currently)
        - a content block delta event (for chunks of text in a message or tool call arg)
        - usage metric information

        For more info, see bedrock converse response stream response syntax:
        https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_ConverseStream.html#API_runtime_ConverseStream_ResponseSyntax
        """
        usage_metrics: Dict[str, int] = {}
        metadata: Dict[str, str] = {}
        messages: List[Dict[str, Any]] = []

        text_content_blocks: Dict[int, str] = {}
        tool_content_blocks: Dict[int, Dict[str, Any]] = {}

        current_message: Optional[Dict[str, Any]] = None

        for chunk in streamed_body:
            if "metadata" in chunk and "usage" in chunk["metadata"]:
                usage = chunk["metadata"]["usage"]
                for token_type in ("input", "output", "total"):
                    if "{}Tokens".format(token_type) in usage:
                        usage_metrics["{}_tokens".format(token_type)] = usage["{}Tokens".format(token_type)]

            if "messageStart" in chunk:
                message_data = chunk["messageStart"]
                current_message = {"role": message_data.get("role", "assistant"), "content_block_indicies": []}

            # always make sure we have a current message
            if current_message is None:
                current_message = {"role": "assistant", "content_block_indicies": []}

            if "contentBlockStart" in chunk:
                block_start = chunk["contentBlockStart"]
                index = block_start.get("contentBlockIndex")

                if index is not None:
                    current_message["content_block_indicies"].append(index)
                    if "start" in block_start and "toolUse" in block_start["start"]:
                        tool_content_blocks[index] = block_start["start"]["toolUse"]

            if "contentBlockDelta" in chunk:
                content_block_delta = chunk["contentBlockDelta"]
                index = content_block_delta.get("contentBlockIndex")

                if index is not None and "delta" in content_block_delta:
                    if index not in current_message.get("content_block_indicies", []):
                        current_message["content_block_indicies"].append(index)

                    delta_content = content_block_delta["delta"]
                    text_content_blocks[index] = text_content_blocks.get(index, "") + delta_content.get("text", "")

                    if delta_content.get("toolUse", {}).get("input"):
                        tool_content_blocks[index] = tool_content_blocks.get(index, {})
                        tool_content_blocks[index]["input"] = (
                            tool_content_blocks[index].get("input", "") + delta_content["toolUse"]["input"]
                        )

            if "messageStop" in chunk:
                messages.append(
                    get_final_message_converse_stream_message(current_message, text_content_blocks, tool_content_blocks)
                )
                current_message = None

        # Handle the case where we didn't receive an explicit message stop event
        if current_message is not None and current_message.get("content_block_indicies"):
            messages.append(
                get_final_message_converse_stream_message(current_message, text_content_blocks, tool_content_blocks)
            )

        if not messages:
            messages.append({"role": "assistant", "content": ""})

        return messages, metadata, usage_metrics

    @staticmethod
    def _extract_input_message(prompt):
        """Extract input messages from the stored prompt.
        Anthropic allows for messages and multiple texts in a message, which requires some special casing.
        """
        if isinstance(prompt, str):
            return [{"content": prompt}]
        if not isinstance(prompt, list):
            log.warning("Bedrock input is not a list of messages or a string.")
            return [{"content": ""}]
        input_messages = []
        for p in prompt:
            content = p.get("content", "")
            if isinstance(content, list) and isinstance(content[0], dict):
                for entry in content:
                    if entry.get("type") == "text":
                        input_messages.append({"content": entry.get("text", ""), "role": str(p.get("role", ""))})
                    elif entry.get("type") == "image":
                        # Store a placeholder for potentially enormous binary image data.
                        input_messages.append({"content": "([IMAGE DETECTED])", "role": str(p.get("role", ""))})
            else:
                input_messages.append({"content": content, "role": str(p.get("role", ""))})
        return input_messages

    @staticmethod
    def _extract_output_message(response):
        """Extract output messages from the stored response.
        Anthropic allows for chat messages, which requires some special casing.
        """
        if isinstance(response["text"], str):
            return [{"content": response["text"]}]
        if isinstance(response["text"], list):
            if isinstance(response["text"][0], str):
                return [{"content": str(content)} for content in response["text"]]
            if isinstance(response["text"][0], dict):
                return [{"content": response["text"][0].get("text", "")}]

    def is_default_base_url(self, base_url: Optional[str] = None) -> bool:
        if base_url is None:
            return True

        parsed_url = urlparse(base_url)
        default_url_regex = re.compile(BEDROCK_URL_REGEX_PATTERN)
        return default_url_regex.match(parsed_url.hostname or "") is not None
