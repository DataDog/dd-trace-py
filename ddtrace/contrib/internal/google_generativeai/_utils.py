import sys

from google.generativeai.types.generation_types import to_generation_config_dict
import wrapt

from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._utils import _get_attr


class BaseTracedGenerateContentResponse(wrapt.ObjectProxy):
    """Base wrapper class for GenerateContentResponse objects for tracing streamed responses."""

    def __init__(self, wrapped, instance, integration, span, args, kwargs):
        super().__init__(wrapped)
        self._model_instance = instance
        self._dd_integration = integration
        self._dd_span = span
        self._args = args
        self._kwargs = kwargs


class TracedGenerateContentResponse(BaseTracedGenerateContentResponse):
    def __iter__(self):
        try:
            for chunk in self.__wrapped__.__iter__():
                yield chunk
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            raise
        else:
            tag_response(self._dd_span, self.__wrapped__, self._dd_integration, self._model_instance)
        finally:
            self._kwargs["instance"] = self._model_instance
            self._dd_integration.llmobs_set_tags(
                self._dd_span,
                args=self._args,
                kwargs=self._kwargs,
                response=self.__wrapped__,
            )
            self._dd_span.finish()


class TracedAsyncGenerateContentResponse(BaseTracedGenerateContentResponse):
    async def __aiter__(self):
        try:
            async for chunk in self.__wrapped__.__aiter__():
                yield chunk
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            raise
        else:
            tag_response(self._dd_span, self.__wrapped__, self._dd_integration, self._model_instance)
        finally:
            self._kwargs["instance"] = self._model_instance
            self._dd_integration.llmobs_set_tags(
                self._dd_span,
                args=self._args,
                kwargs=self._kwargs,
                response=self.__wrapped__,
            )
            self._dd_span.finish()


def _extract_model_name(instance):
    """Extract the model name from the instance.
    The Google Gemini Python SDK stores model names in the format `"models/{model_name}"`
    so we do our best to return the model name instead of the full string.
    """
    model_name = getattr(instance, "model_name", "")
    if not model_name or not isinstance(model_name, str):
        return ""
    if "/" in model_name:
        return model_name.split("/")[-1]
    return model_name


def _extract_api_key(instance):
    """Extract the API key from the model instance."""
    client = getattr(instance, "_client", None)
    if getattr(instance, "_async_client", None):
        client = getattr(instance._async_client, "_client", None)
    if not client:
        return None
    client_options = getattr(client, "_client_options", None)
    if not client_options:
        return None
    return getattr(client_options, "api_key", None)


def _tag_request_content_part(span, integration, part, part_idx, content_idx):
    """Tag the generation span with request content parts."""
    text = _get_attr(part, "text", "")
    function_call = _get_attr(part, "function_call", None)
    function_response = _get_attr(part, "function_response", None)
    span.set_tag_str(
        "google_generativeai.request.contents.%d.parts.%d.text" % (content_idx, part_idx), integration.trunc(str(text))
    )
    if function_call:
        function_call_dict = type(function_call).to_dict(function_call)
        span.set_tag_str(
            "google_generativeai.request.contents.%d.parts.%d.function_call.name" % (content_idx, part_idx),
            integration.trunc(str(function_call_dict.get("name", ""))),
        )
        span.set_tag_str(
            "google_generativeai.request.contents.%d.parts.%d.function_call.args" % (content_idx, part_idx),
            integration.trunc(str(function_call_dict.get("args", {}))),
        )
    if function_response:
        function_response_dict = type(function_response).to_dict(function_response)
        span.set_tag_str(
            "google_generativeai.request.contents.%d.parts.%d.function_response.name" % (content_idx, part_idx),
            str(function_response_dict.get("name", "")),
        )
        span.set_tag_str(
            "google_generativeai.request.contents.%d.parts.%d.function_response.response" % (content_idx, part_idx),
            integration.trunc(str(function_response_dict.get("response", {}))),
        )


def _tag_request_content(span, integration, content, content_idx):
    """Tag the generation span with request contents."""
    if isinstance(content, str):
        span.set_tag_str("google_generativeai.request.contents.%d.text" % content_idx, integration.trunc(content))
        return
    if isinstance(content, dict):
        role = content.get("role", "")
        if role:
            span.set_tag_str("google_generativeai.request.contents.%d.role" % content_idx, str(content.get("role", "")))
        parts = content.get("parts", [])
        for part_idx, part in enumerate(parts):
            _tag_request_content_part(span, integration, part, part_idx, content_idx)
        return
    role = getattr(content, "role", "")
    if role:
        span.set_tag_str("google_generativeai.request.contents.%d.role" % content_idx, str(role))
    parts = getattr(content, "parts", [])
    if not parts:
        span.set_tag_str(
            "google_generativeai.request.contents.%d.text" % content_idx,
            integration.trunc("[Non-text content object: {}]".format(repr(content))),
        )
        return
    for part_idx, part in enumerate(parts):
        _tag_request_content_part(span, integration, part, part_idx, content_idx)


def _tag_response_part(span, integration, part, part_idx, candidate_idx):
    """Tag the generation span with response part text and function calls."""
    text = part.get("text", "")
    span.set_tag_str(
        "google_generativeai.response.candidates.%d.content.parts.%d.text" % (candidate_idx, part_idx),
        integration.trunc(str(text)),
    )
    function_call = part.get("function_call", None)
    if not function_call:
        return
    span.set_tag_str(
        "google_generativeai.response.candidates.%d.content.parts.%d.function_call.name" % (candidate_idx, part_idx),
        integration.trunc(str(function_call.get("name", ""))),
    )
    span.set_tag_str(
        "google_generativeai.response.candidates.%d.content.parts.%d.function_call.args" % (candidate_idx, part_idx),
        integration.trunc(str(function_call.get("args", {}))),
    )


def tag_request(span, integration, instance, args, kwargs):
    """Tag the generation span with request details.
    Includes capturing generation configuration, system prompt, and messages.
    """
    contents = get_argument_value(args, kwargs, 0, "contents")
    generation_config = kwargs.get("generation_config", {})
    system_instruction = getattr(instance, "_system_instruction", None)
    stream = kwargs.get("stream", None)

    try:
        generation_config_dict = to_generation_config_dict(generation_config)
    except TypeError:
        generation_config_dict = None
    if generation_config_dict is not None:
        for k, v in generation_config_dict.items():
            span.set_tag_str("google_generativeai.request.generation_config.%s" % k, str(v))

    if stream:
        span.set_tag("google_generativeai.request.stream", True)

    if not integration.is_pc_sampled_span(span):
        return

    if system_instruction:
        for idx, part in enumerate(system_instruction.parts):
            span.set_tag_str(
                "google_generativeai.request.system_instruction.%d.text" % idx, integration.trunc(str(part.text))
            )

    if isinstance(contents, str):
        span.set_tag_str("google_generativeai.request.contents.0.text", integration.trunc(contents))
        return
    elif isinstance(contents, dict):
        span.set_tag_str("google_generativeai.request.contents.0.text", integration.trunc(str(contents)))
        return
    elif not isinstance(contents, list):
        return
    for content_idx, content in enumerate(contents):
        _tag_request_content(span, integration, content, content_idx)


def tag_response(span, generations, integration, instance):
    """Tag the generation span with response details.
    Includes capturing generation text, roles, finish reasons, and token counts.
    """
    api_key = _extract_api_key(instance)
    if api_key:
        span.set_tag("google_generativeai.request.api_key", "...{}".format(api_key[-4:]))

    generations_dict = generations.to_dict()
    for candidate_idx, candidate in enumerate(generations_dict.get("candidates", [])):
        finish_reason = candidate.get("finish_reason", None)
        if finish_reason:
            span.set_tag_str(
                "google_generativeai.response.candidates.%d.finish_reason" % candidate_idx, str(finish_reason)
            )
        candidate_content = candidate.get("content", {})
        role = candidate_content.get("role", "")
        span.set_tag_str("google_generativeai.response.candidates.%d.content.role" % candidate_idx, str(role))
        if not integration.is_pc_sampled_span(span):
            continue
        parts = candidate_content.get("parts", [])
        for part_idx, part in enumerate(parts):
            _tag_response_part(span, integration, part, part_idx, candidate_idx)

    token_counts = generations_dict.get("usage_metadata", None)
    if not token_counts:
        return
    span.set_metric("google_generativeai.response.usage.prompt_tokens", token_counts.get("prompt_token_count", 0))
    span.set_metric(
        "google_generativeai.response.usage.completion_tokens", token_counts.get("candidates_token_count", 0)
    )
    span.set_metric("google_generativeai.response.usage.total_tokens", token_counts.get("total_token_count", 0))
