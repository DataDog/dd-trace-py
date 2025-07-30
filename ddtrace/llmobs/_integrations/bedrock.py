from typing import Any
from typing import Dict
from typing import Generator
from typing import List
from typing import Optional
from typing import Tuple

from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._constants import CACHE_READ_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import CACHE_WRITE_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import INTEGRATION
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import PROXY_REQUEST
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import TAGS
from ddtrace.llmobs._integrations import BaseLLMIntegration
from ddtrace.llmobs._integrations.bedrock_agents import _create_or_update_bedrock_trace_step_span
from ddtrace.llmobs._integrations.bedrock_agents import _extract_trace_step_id
from ddtrace.llmobs._integrations.bedrock_agents import translate_bedrock_trace
from ddtrace.llmobs._integrations.bedrock_utils import normalize_input_tokens
from ddtrace.llmobs._integrations.utils import get_final_message_converse_stream_message
from ddtrace.llmobs._integrations.utils import get_messages_from_converse_content
from ddtrace.llmobs._integrations.utils import update_proxy_workflow_input_output_value
from ddtrace.llmobs._telemetry import record_bedrock_agent_span_event_created
from ddtrace.llmobs._writer import LLMObsSpanEvent
from ddtrace.trace import Span


log = get_logger(__name__)


class BedrockIntegration(BaseLLMIntegration):
    _integration_name = "bedrock"
    _spans: Dict[str, LLMObsSpanEvent] = {}  # Maps LLMObs span ID to LLMObs span events
    _active_span_by_step_id: Dict[str, LLMObsSpanEvent] = {}  # Maps trace step ID to currently active span

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
            "llmobs.proxy_request": Optional[bool],
        }
        """
        if operation == "agent":
            return self._llmobs_set_tags_agent(span, args, kwargs, response)

        metadata = {}
        usage_metrics = {}
        ctx = args[0]

        span_kind = "workflow" if ctx.get_item(PROXY_REQUEST) else "llm"

        request_params = ctx.get_item("llmobs.request_params") or {}

        if ctx.get_item("llmobs.stop_reason"):
            metadata["stop_reason"] = ctx["llmobs.stop_reason"]
        if ctx.get_item("llmobs.usage"):
            usage_metrics = ctx["llmobs.usage"]

        normalize_input_tokens(usage_metrics)

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
                """
                At this point, we signal to `_converse_output_stream_processor` that we're done with the stream
                and ready to get the final results. This causes `_converse_output_stream_processor` to break out of the
                while loop, do some final processing, and return the final results.
                """
                try:
                    response.send(None)
                except StopIteration as e:
                    output_messages, additional_metadata, streamed_usage_metrics = e.value
                finally:
                    response.close()

                metadata.update(additional_metadata)
                usage_metrics.update(streamed_usage_metrics)
            else:
                output_messages = self._extract_output_message(response)

        span._set_ctx_items(
            {
                SPAN_KIND: span_kind,
                MODEL_NAME: ctx.get_item("model_name") or "",
                MODEL_PROVIDER: ctx.get_item("model_provider") or "",
                INPUT_MESSAGES: input_messages,
                METADATA: metadata,
                METRICS: usage_metrics if span_kind != "workflow" else {},
                OUTPUT_MESSAGES: output_messages,
            }
        )

        update_proxy_workflow_input_output_value(span, span_kind)

    def _llmobs_set_tags_agent(self, span, args, kwargs, response):
        if not self.llmobs_enabled or not span:
            return
        input_args = get_argument_value(args, kwargs, 1, "inputArgs", optional=True) or {}
        input_value = input_args.get("inputText", "")
        agent_id = input_args.get("agentId", "")
        agent_alias_id = input_args.get("agentAliasId", "")
        session_id = input_args.get("sessionId", "")
        span._set_ctx_items(
            {
                SPAN_KIND: "agent",
                INPUT_VALUE: str(input_value),
                TAGS: {"session_id": session_id},
                METADATA: {"agent_id": agent_id, "agent_alias_id": agent_alias_id},
                INTEGRATION: "bedrock_agents",
            }
        )
        if not response:
            return
        span._set_ctx_item(OUTPUT_VALUE, str(response))

    def translate_bedrock_traces(self, traces, root_span) -> None:
        """Translate bedrock agent traces to LLMObs span events."""
        if not traces or not self.llmobs_enabled:
            return
        for trace in traces:
            trace_step_id = _extract_trace_step_id(trace)
            current_active_span_event = self._active_span_by_step_id.pop(trace_step_id, None)
            translated_span_event, finished = translate_bedrock_trace(
                trace, root_span, current_active_span_event, trace_step_id
            )
            if translated_span_event:
                self._spans[translated_span_event["span_id"]] = translated_span_event
                if not finished:
                    self._active_span_by_step_id[trace_step_id] = translated_span_event
            _create_or_update_bedrock_trace_step_span(
                trace, trace_step_id, translated_span_event, root_span, self._spans
            )
        for _, span_event in self._spans.items():
            LLMObs._instance._llmobs_span_writer.enqueue(span_event)
            record_bedrock_agent_span_event_created(span_event)
        self._spans.clear()
        self._active_span_by_step_id.clear()

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
    def _converse_output_stream_processor() -> (
        Generator[
            None,
            Dict[str, Any],
            Tuple[List[Dict[str, Any]], Dict[str, str], Dict[str, int]],
        ]
    ):
        """
        Listens for output chunks from a converse streamed response and builds a
        list of output messages, usage metrics, and metadata.

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

        chunk = yield

        while chunk is not None:
            if "metadata" in chunk and "usage" in chunk["metadata"]:
                usage = chunk["metadata"]["usage"]
                for token_type in ("input", "output", "total"):
                    if "{}Tokens".format(token_type) in usage:
                        usage_metrics["{}_tokens".format(token_type)] = usage["{}Tokens".format(token_type)]

                cache_read_tokens = usage.get("cacheReadInputTokenCount", None) or usage.get(
                    "cacheReadInputTokens", None
                )
                cache_write_tokens = usage.get("cacheWriteInputTokenCount", None) or usage.get(
                    "cacheWriteInputTokens", None
                )

                if cache_read_tokens is not None:
                    usage_metrics[CACHE_READ_INPUT_TOKENS_METRIC_KEY] = cache_read_tokens
                if cache_write_tokens is not None:
                    usage_metrics[CACHE_WRITE_INPUT_TOKENS_METRIC_KEY] = cache_write_tokens

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

            chunk = yield

        # Handle the case where we didn't receive an explicit message stop event
        if current_message is not None and current_message.get("content_block_indicies"):
            messages.append(
                get_final_message_converse_stream_message(current_message, text_content_blocks, tool_content_blocks)
            )

        if not messages:
            messages.append({"role": "assistant", "content": ""})

        normalize_input_tokens(usage_metrics)
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

    def _get_base_url(self, **kwargs: Dict[str, Any]) -> Optional[str]:
        instance = kwargs.get("instance")
        endpoint = getattr(instance, "_endpoint", None)
        endpoint_host = getattr(endpoint, "host", None) if endpoint else None
        return str(endpoint_host) if endpoint_host else None

    def _tag_proxy_request(self, ctx: core.ExecutionContext) -> None:
        base_url = self._get_base_url(instance=ctx.get_item("instance"))
        if self._is_instrumented_proxy_url(base_url):
            ctx.set_item(PROXY_REQUEST, True)
