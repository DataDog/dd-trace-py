from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace._trace.span import Span
from ddtrace.contrib.internal.google_genai._utils import extract_metrics_google_genai
from ddtrace.contrib.internal.google_genai._utils import extract_provider_and_model_name
from ddtrace.contrib.internal.google_genai._utils import normalize_contents
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._utils import _get_attr


# some parameters that we do not include in the config: system_instruction, tools, etc...
# https://cloud.google.com/vertex-ai/generative-ai/docs/multimodal/content-generation-parameters
METADATA_PARAMS = [
    "temperature",
    "top_p",
    "top_k",
    "candidate_count",
    "max_output_tokens",
    "stop_sequences",
    "response_logprobs",
    "logprobs",
    "presence_penalty",
    "frequency_penalty",
    "seed",
    "response_mime_type",
    "safety_settings",
    "automatic_function_calling",
]


class GoogleGenAIIntegration(BaseLLMIntegration):
    _integration_name = "google_genai"

    def _set_base_span_tags(
        self, span: Span, provider: Optional[str] = None, model: Optional[str] = None, **kwargs: Dict[str, Any]
    ) -> None:
        if provider is not None:
            span.set_tag_str("google_genai.request.provider", provider)
        if model is not None:
            span.set_tag_str("google_genai.request.model", model)

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        config = get_argument_value(args, kwargs, -1, "config", optional=True)
        provider_name, model_name = extract_provider_and_model_name(kwargs)

        span._set_ctx_items(
            {
                SPAN_KIND: "llm",
                MODEL_NAME: model_name,
                MODEL_PROVIDER: provider_name,
                METADATA: self._extract_metadata(config),
                INPUT_MESSAGES: self._extract_input_message(args, kwargs, config),
                OUTPUT_MESSAGES: self._extract_output_message(response),
                METRICS: extract_metrics_google_genai(response),
            }
        )

    def _extract_message_from_part_google_genai(self, part, role):
        """part is a PartUnion = Union[File, Part, PIL_Image, str]"""
        message = {"role": role}
        if isinstance(part, str):
            message["content"] = part
            return message

        text = _get_attr(part, "text", None)
        if text:
            message["content"] = text
            return message

        function_call = _get_attr(part, "function_call", None)
        if function_call:
            function_call_dict = function_call.model_dump()
            message["tool_calls"] = [
                {"name": function_call_dict.get("name", ""), "arguments": function_call_dict.get("args", {})}
            ]
            return message

        function_response = _get_attr(part, "function_response", None)
        if function_response:
            function_response_dict = function_response.model_dump()
            message["content"] = "[tool result: {}]".format(function_response_dict.get("response", ""))
            return message

        executable_code = _get_attr(part, "executable_code", None)
        if executable_code:
            language = _get_attr(executable_code, "language", "UNKNOWN")
            code = _get_attr(executable_code, "code", "")
            message["content"] = "[executable code ({language}): {code}]".format(language=language, code=code)
            return message

        code_execution_result = _get_attr(part, "code_execution_result", None)
        if code_execution_result:
            outcome = _get_attr(code_execution_result, "outcome", "OUTCOME_UNSPECIFIED")
            output = _get_attr(code_execution_result, "output", "")
            message["content"] = "[code execution result ({outcome}): {output}]".format(outcome=outcome, output=output)
            return message

        thought = _get_attr(part, "thought", None)
        # thought is just a boolean indicating if the part was a thought
        if thought:
            message["content"] = "[thought: {}]".format(thought)
            return message

        return {"content": "Unsupported file type: {}".format(type(part)), "role": role}

    def _extract_input_message(self, args, kwargs, config):
        messages = []
        # system instruction in config
        if config is not None:
            system_instruction = _get_attr(config, "system_instruction", None)
            if system_instruction is not None:
                normalized_sys = normalize_contents(system_instruction)

                for content in normalized_sys:
                    role = content.get("role") or "system"
                    parts = content.get("parts", [])

                    for part in parts:
                        message = self._extract_message_from_part_google_genai(part, role)
                        messages.append(message)
        # user input messages
        contents = get_argument_value(args, kwargs, -1, "contents")
        normalized_contents = normalize_contents(contents)
        for content in normalized_contents:
            role = content.get("role") or "user"
            parts = content.get("parts", [])

            for part in parts:
                message = self._extract_message_from_part_google_genai(part, role)
                messages.append(message)
        return messages

    def _extract_output_message(self, response):
        if not response:
            return [{"content": ""}]
        # streamed responses will be a list of chunks
        if isinstance(response, list):
            message_content = []
            tool_calls = []
            role = "model"
            # response is a list of GenerateContentResponse chunks
            for r in response:
                messages = self._process_response(r)
                for message in messages:
                    message_content.append(message.get("content", ""))
                    tool_calls.extend(message.get("tool_calls", []))
            message = {"content": "".join(message_content), "role": role}
            if tool_calls:
                message["tool_calls"] = tool_calls
            return [message]
        # non-streamed responses will be a single GenerateContentResponse
        return self._process_response(response)

    def _process_response(self, response):
        messages = []
        candidates = _get_attr(response, "candidates", [])
        for candidate in candidates:
            content = _get_attr(candidate, "content", None)
            if not content:
                continue
            parts = _get_attr(content, "parts", [])
            role = _get_attr(content, "role", None) or "model"
            for part in parts:
                message = self._extract_message_from_part_google_genai(part, role)
                messages.append(message)
        return messages

    def _extract_metadata(self, config):
        if not config:
            return {}
        metadata = {}
        for param in METADATA_PARAMS:
            metadata[param] = _get_attr(config, param, None)
        return metadata
