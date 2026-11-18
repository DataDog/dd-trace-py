from collections.abc import Mapping
import json
from typing import Any
from typing import Optional

from ddtrace.internal.native._native import SpanData
from ddtrace.internal.utils.formats import _get_attr
from ddtrace.llmobs._constants import AUDIO_FALLBACK_MARKER
from ddtrace.llmobs._constants import CACHE_READ_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import FILE_FALLBACK_MARKER
from ddtrace.llmobs._constants import IMAGE_DETECTED_MARKER
from ddtrace.llmobs._constants import IMAGE_FALLBACK_MARKER
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._integrations.google_utils import GOOGLE_GENAI_DEFAULT_MODEL_ROLE
from ddtrace.llmobs._integrations.google_utils import extract_embedding_metrics_google_genai
from ddtrace.llmobs._integrations.google_utils import extract_generation_metrics_google_genai
from ddtrace.llmobs._integrations.google_utils import extract_message_from_part_google_genai
from ddtrace.llmobs._integrations.google_utils import extract_provider_and_model_name
from ddtrace.llmobs._integrations.google_utils import normalize_contents_google_genai
from ddtrace.llmobs._integrations.utils import format_image_part_with_guard
from ddtrace.llmobs._span import _LLMObsSpan
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import load_data_value
from ddtrace.llmobs._utils import safe_json
from ddtrace.llmobs.types import Document
from ddtrace.llmobs.types import ImagePart
from ddtrace.llmobs.types import Message
from ddtrace.llmobs.types import ToolDefinition


# https://cloud.google.com/vertex-ai/generative-ai/docs/multimodal/content-generation-parameters
GENERATE_METADATA_PARAMS = [
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

EMBED_METADATA_PARAMS = [
    "task_type",
    "title",
    "output_dimensionality",
    "mime_type",
    "auto_truncate",
]


DECISION_MODEL_PROVIDER = "google"
DECISION_PROVIDER_TAG = "decision_provider"
DECISION_REQUEST_KEYS = ("model", "input", "questions")
DECISION_MEDIA_MARKERS = {
    "image": IMAGE_FALLBACK_MARKER,
    "audio": AUDIO_FALLBACK_MARKER,
    "video": "[video]",
    "document": FILE_FALLBACK_MARKER,
}


class GoogleGenAIIntegration(BaseLLMIntegration):
    _integration_name = "google_genai"

    def _set_base_span_tags(self, span: SpanData, **kwargs: Any) -> None:
        """Nothing to do: the contrib sets the APM model/provider tags itself."""

    def _llmobs_set_tags(
        self,
        span: _LLMObsSpan,
        args: list[Any],
        kwargs: dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        if operation == "decision":
            self._llmobs_set_tags_from_decision(span, kwargs, response)
            return
        provider_name, model_name = extract_provider_and_model_name(kwargs=kwargs)
        if response is not None:
            model_name = getattr(response, "model_version", "") or model_name
        _annotate_llmobs_span_data(
            span,
            kind=operation,
            model_name=model_name,
            model_provider=provider_name,
        )
        if operation == "embedding":
            self._llmobs_set_tags_from_embedding(span, args, kwargs, response)
        elif operation == "llm":
            self._llmobs_set_tags_from_llm(span, args, kwargs, response)

    def _set_apm_shadow_tags(self, span, args, kwargs, response=None, operation=""):
        if operation == "embedding":
            metrics = extract_embedding_metrics_google_genai(response)
        elif operation == "llm":
            metrics = extract_generation_metrics_google_genai(response)
        elif operation == "decision":
            _, model_name = extract_provider_and_model_name(kwargs=kwargs)
            usage = _get_attr(_decision_response_data(response), "usage", None)
            self._apply_shadow_metrics(
                span, _decision_metrics(usage), "llm", model_name=model_name, model_provider=DECISION_MODEL_PROVIDER
            )
            return
        else:
            return
        provider_name, model_name = extract_provider_and_model_name(kwargs=kwargs)
        if response is not None:
            model_name = getattr(response, "model_version", "") or model_name
        self._apply_shadow_metrics(
            span,
            metrics,
            operation,
            model_name=model_name,
            model_provider=provider_name,
        )

    def _llmobs_set_tags_from_llm(self, span, args, kwargs, response):
        config = kwargs.get("config")
        tools = self._extract_tools(config)
        _annotate_llmobs_span_data(
            span,
            metadata=self._extract_metadata(config, GENERATE_METADATA_PARAMS),
            input_messages=self._extract_input_messages(args, kwargs, config),
            output_messages=self._extract_output_messages(response),
            metrics=extract_generation_metrics_google_genai(response),
            **({"tool_definitions": tools} if tools else {}),
        )

    def _llmobs_set_tags_from_decision(self, span, kwargs, response):
        """Record the decisions request body as the input and the response body, without usage, as the output.

        Inline media in the input is replaced by a marker, and inline images are captured as image parts. Only request
        fields are taken from extra_body, since its other fields can carry credentials.
        """
        _, model_name = extract_provider_and_model_name(kwargs=kwargs)
        body = {k: kwargs[k] for k in DECISION_REQUEST_KEYS if k in kwargs}
        extra_body = kwargs.get("extra_body")
        if isinstance(extra_body, Mapping):
            body.update({k: v for k, v in extra_body.items() if k in DECISION_REQUEST_KEYS})
        image_parts: list[ImagePart] = []
        request = {k: _decision_input(v, image_parts) if k == "input" else load_data_value(v) for k, v in body.items()}
        input_message = Message(role="user", content=safe_json(request) or "")
        if image_parts:
            input_message["image_parts"] = image_parts

        output_messages = [Message(role="assistant", content="")]
        metrics: dict[str, Any] = {}
        output = _decision_response_data(response)
        if isinstance(output, dict):
            metrics = _decision_metrics(output.pop("usage", None))
            output_messages = [Message(role="assistant", content=safe_json(output) or "")]

        _annotate_llmobs_span_data(
            span,
            kind="llm",
            model_name=model_name,
            model_provider=DECISION_MODEL_PROVIDER,
            input_messages=[input_message],
            output_messages=output_messages,
            metrics=metrics,
            tags={DECISION_PROVIDER_TAG: DECISION_MODEL_PROVIDER},
        )

    def _llmobs_set_tags_from_embedding(self, span, args, kwargs, response):
        config = kwargs.get("config")
        _annotate_llmobs_span_data(
            span,
            metadata=self._extract_metadata(config, EMBED_METADATA_PARAMS),
            input_documents=self._extract_embedding_input_documents(args, kwargs, config),
            output_value=self._extract_embedding_output_value(response),
            metrics=extract_embedding_metrics_google_genai(response),
        )

    def _extract_input_messages(self, args: list[Any], kwargs: dict[str, Any], config) -> list[Message]:
        messages: list[Message] = []

        system_instruction = _get_attr(config, "system_instruction", None)
        if system_instruction is not None:
            messages.extend(self._extract_messages_from_contents(system_instruction, "system"))

        contents = kwargs.get("contents")
        messages.extend(self._extract_messages_from_contents(contents, "user"))

        return messages

    def _extract_messages_from_contents(self, contents, default_role: str) -> list[Message]:
        messages: list[Message] = []
        for content in normalize_contents_google_genai(contents):
            role = content.get("role") or default_role
            for part in content.get("parts", []):
                messages.append(extract_message_from_part_google_genai(part, role))
        return messages

    def _extract_output_messages(self, response) -> list[Message]:
        if not response:
            return [Message(content="", role=GOOGLE_GENAI_DEFAULT_MODEL_ROLE)]
        messages = []
        candidates = _get_attr(response, "candidates", []) or []
        for candidate in candidates:
            content = _get_attr(candidate, "content", None)
            if not content:
                continue
            parts = _get_attr(content, "parts", []) or []
            role = _get_attr(content, "role", GOOGLE_GENAI_DEFAULT_MODEL_ROLE)
            for part in parts:
                message = extract_message_from_part_google_genai(part, role)
                messages.append(message)
        return messages

    def _extract_embedding_output_value(self, response) -> str:
        embeddings = _get_attr(response, "embeddings", [])
        if embeddings:
            embedding_dim = len(embeddings[0].values)
            return f"[{len(embeddings)} embedding(s) returned with size {embedding_dim}]"
        return ""

    def _extract_embedding_input_documents(self, args, kwargs, config) -> list[Document]:
        contents = kwargs.get("contents")
        messages = self._extract_messages_from_contents(contents, "user")
        documents = [Document(text=str(message.get("content", ""))) for message in messages]
        return documents

    def _extract_metadata(self, config, params) -> dict[str, Any]:
        if not config:
            return {}
        metadata = {}
        for param in params:
            metadata[param] = _get_attr(config, param, None)
        return metadata

    def _function_declaration_to_tool_definition(self, function_declaration) -> ToolDefinition:
        schema = _get_attr(function_declaration, "parameters", {}) or {}
        if hasattr(schema, "model_dump"):
            schema = schema.model_dump(exclude_none=True)
        else:
            schema = {"value": repr(schema)}

        return ToolDefinition(
            name=str(_get_attr(function_declaration, "name", "") or ""),
            description=str(_get_attr(function_declaration, "description", "") or ""),
            schema=schema,
        )

    def _extract_tools(self, config) -> list[ToolDefinition]:
        try:
            from google.genai.types import FunctionDeclaration
        except ImportError:
            FunctionDeclaration = None
        tool_definitions = []
        tools = _get_attr(config, "tools", []) or []
        for tool in tools:
            if (
                callable(tool)
                and FunctionDeclaration is not None
                and hasattr(FunctionDeclaration, "from_callable_with_api_option")
            ):
                function_declaration = FunctionDeclaration.from_callable_with_api_option(
                    callable=tool, api_option="GEMINI_API"
                )
                tool_definition_info = self._function_declaration_to_tool_definition(function_declaration)
                tool_definitions.append(tool_definition_info)
            else:
                function_declarations = _get_attr(tool, "function_declarations", []) or []
                for function_declaration in function_declarations:
                    tool_definition_info = self._function_declaration_to_tool_definition(function_declaration)
                    tool_definitions.append(tool_definition_info)
        return tool_definitions


def _decision_input(value: Any, image_parts: list[ImagePart]) -> Any:
    """Return the decision input as JSON data, with inline media data replaced by a marker."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(value, Mapping):
        media_type = value.get("type")
        if isinstance(media_type, str) and media_type in DECISION_MEDIA_MARKERS and value.get("data") is not None:
            return {
                **load_data_value({k: v for k, v in value.items() if k != "data"}),
                "data": _media_marker(value, image_parts),
            }
        return {str(k): _decision_input(v, image_parts) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_decision_input(v, image_parts) for v in value]
    return load_data_value(value)


def _media_marker(part: Mapping[str, Any], image_parts: list[ImagePart]) -> str:
    """Capture inline base64 image data as an image part; file and path data is never read."""
    data = part.get("data")
    media_type = part["type"]
    if media_type == "image" and isinstance(data, str):
        image_part = format_image_part_with_guard(data, part.get("mime_type") or "")
        if image_part:
            image_parts.append(image_part)
            return IMAGE_DETECTED_MARKER
    return DECISION_MEDIA_MARKERS[media_type]


def _decision_response_data(response: Any) -> Optional[Any]:
    """Return the decisions response body as JSON data, including for raw responses whose body was read."""
    if response is None:
        return None
    http_response = getattr(response, "http_response", None)
    if http_response is not None:
        try:
            return json.loads(http_response.content)
        except Exception:
            # A streamed raw response is not read yet, and reading it here would consume it for the caller
            return None
    if hasattr(response, "model_dump"):
        return response.model_dump(mode="json", by_alias=True, exclude_none=True)
    return load_data_value(response)


def _decision_metrics(usage: Any) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for metric_key, usage_key in (
        (INPUT_TOKENS_METRIC_KEY, "total_input_tokens"),
        (OUTPUT_TOKENS_METRIC_KEY, "total_output_tokens"),
        (TOTAL_TOKENS_METRIC_KEY, "total_tokens"),
        (CACHE_READ_INPUT_TOKENS_METRIC_KEY, "total_cached_tokens"),
    ):
        value = _get_attr(usage, usage_key, None)
        if isinstance(value, int):
            metrics[metric_key] = value
    return metrics
