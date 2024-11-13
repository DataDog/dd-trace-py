import sys
import copy

import vertexai
from vertexai.generative_models import GenerativeModel, Part

from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._utils import _get_attr


class BaseTracedVertexAIStreamResponse:
    def __init__(self, generator, instance, integration, span):
        self._generator = generator
        self._model_instance = instance
        self._dd_integration = integration
        self._dd_span = span
        self._chunks = []


class TracedVertexAIStreamResponse(BaseTracedVertexAIStreamResponse):
    def __enter__(self):
        self._generator.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._generator.__exit__(exc_type, exc_val, exc_tb)

    def __iter__(self):
        try:
            for chunk in self._generator.__iter__():
                self._chunks.append(copy.deepcopy(chunk))
                yield chunk
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            raise
        else:
            tag_stream_response(self._dd_span, self._chunks, self._dd_integration)
        self._dd_span.finish()


class TracedAsyncVertexAIStreamResponse(BaseTracedVertexAIStreamResponse):
    def __aenter__(self):
        self._generator.__enter__()
        return self

    def __aexit__(self, exc_type, exc_val, exc_tb):
        self._generator.__exit__(exc_type, exc_val, exc_tb)

    async def __aiter__(self):
        try:
            async for chunk in self._generator.__aiter__():
                self._chunks.append(copy.deepcopy(chunk))
                yield chunk
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            raise
        else:
            tag_stream_response(self._dd_span, self._chunks, self._dd_integration)
        self._dd_span.finish()


def _extract_model_name(instance):
    """Extract the model name from the instance.
    Model names are stored in the format `"models/{model_name}"`
    so we do our best to return the model name instead of the full string.
    """
    model_name = getattr(instance, "_model_name", "")
    if not model_name or not isinstance(model_name, str):
        return ""
    if "/" in model_name:
        return model_name.split("/")[-1]
    return model_name


def get_system_instruction_texts_from_model(instance):
    """
    Extract system instructions from model and convert to []str for tagging.
    """
    raw_system_instructions = getattr(instance, "_system_instruction", [])
    if type(raw_system_instructions) == str:
        return [raw_system_instructions]
    elif type(raw_system_instructions) == Part:
        return [raw_system_instructions.text]
    elif type(raw_system_instructions) != list:
        return []

    system_instructions = []
    for elem in raw_system_instructions:
        if type(elem) == str:
            system_instructions.append(elem)
        elif type(elem) == Part:
            system_instructions.append(elem.text)
    return system_instructions


def get_generation_config_from_model(instance, kwargs):
    """
    The generation config can be defined on the model instance or
    as a kwarg of the request. Therefore, try to extract this information
    from the kwargs and otherwise default to checking the model instance attribute.
    """
    generation_config_arg = kwargs.get("generation_config", {})
    if generation_config_arg != {}:
        return generation_config_arg if isinstance(generation_config_arg, dict) else generation_config_arg.to_dict()
    generation_config_attr = instance._generation_config or {}
    return generation_config_attr if isinstance(generation_config_attr, dict) else generation_config_attr.to_dict()


def extract_info_from_parts(parts):
    """Return concatenated text from parts and function calls."""
    concatenated_text = ""
    function_calls = []
    for part in parts:
        text = getattr(part, "text", "")
        if text:
            concatenated_text += text
        function_call = getattr(part, "function_call", None)
        if function_call is not None:
            function_calls.append(function_call)
    return concatenated_text, function_calls


def _tag_response_parts(span, integration, parts):
    text, function_calls = extract_info_from_parts(parts)
    span.set_tag_str(
        "vertexai.response.candidates.%d.content.parts.%d.text" % (0, 0),
        integration.trunc(str(text)),
    )
    for idx, function_call in enumerate(function_calls):
        span.set_tag_str(
            "vertexai.response.candidates.%d.content.parts.%d.function_calls.%d.function_call.name" % (0, 0, idx),
            integration.trunc(str(getattr(function_call, "name", ""))),
        )
        span.set_tag_str(
            "vertexai.response.candidates.%d.content.parts.%d.function_calls.%d.function_call.args" % (0, 0, idx),
            integration.trunc(str(getattr(function_call, "args", ""))),
        )


def tag_stream_response(span, chunks, integration):
    all_parts = []
    role = ""
    for chunk in chunks:
        for candidate_idx, candidate in enumerate(chunk.candidates):
            finish_reason = candidate.finish_reason
            if finish_reason:
                span.set_tag_str(
                    "vertexai.response.candidates.%d.finish_reason" % (candidate_idx), str(finish_reason.name)
                )
            candidate_content = candidate.content
            role = role or candidate_content.role
            span.set_tag_str("vertexai.response.candidates.%d.content.role" % (candidate_idx), str(role))
            if not integration.is_pc_sampled_span(span):
                continue
            all_parts.extend(candidate_content.parts)
        token_counts = chunk.usage_metadata
        if not token_counts:
            continue
        span.set_metric("vertexai.response.usage.prompt_tokens", token_counts.prompt_token_count)
        span.set_metric("vertexai.response.usage.completion_tokens", token_counts.candidates_token_count)
        span.set_metric("vertexai.response.usage.total_tokens", token_counts.total_token_count)
    _tag_response_parts(span, integration, all_parts)


def _tag_request_content_part(span, integration, part, part_idx, content_idx):
    """Tag the generation span with request content parts."""
    text = _get_attr(part, "text", "")
    function_call = _get_attr(part, "function_call", None)
    function_response = _get_attr(part, "function_response", None)
    span.set_tag_str(
        "vertexai.request.contents.%d.parts.%d.text" % (content_idx, part_idx), integration.trunc(str(text))
    )
    if function_call:
        function_call_dict = type(function_call).to_dict(function_call)
        span.set_tag_str(
            "vertexai.request.contents.%d.parts.%d.function_call.name" % (content_idx, part_idx),
            integration.trunc(str(function_call_dict.get("name", ""))),
        )
        span.set_tag_str(
            "vertexai.request.contents.%d.parts.%d.function_call.args" % (content_idx, part_idx),
            integration.trunc(str(function_call_dict.get("args", {}))),
        )
    if function_response:
        function_response_dict = type(function_response).to_dict(function_response)
        span.set_tag_str(
            "vertexai.request.contents.%d.parts.%d.function_response.name" % (content_idx, part_idx),
            str(function_response_dict.get("name", "")),
        )
        span.set_tag_str(
            "vertexai.request.contents.%d.parts.%d.function_response.response" % (content_idx, part_idx),
            integration.trunc(str(function_response_dict.get("response", {}))),
        )


def _tag_request_content(span, integration, content, content_idx):
    """Tag the generation span with request contents."""
    if isinstance(content, str):
        span.set_tag_str("vertexai.request.contents.%d.text" % content_idx, integration.trunc(content))
        return
    if isinstance(content, dict):
        role = content.get("role", "")
        if role:
            span.set_tag_str("vertexai.request.contents.%d.role" % content_idx, str(content.get("role", "")))
        parts = content.get("parts", [])
        for part_idx, part in enumerate(parts):
            _tag_request_content_part(span, integration, part, part_idx, content_idx)
        return
    if isinstance(content, Part):
        _tag_request_content_part(span, integration, content, 0, content_idx)
        return
    role = getattr(content, "role", "")
    if role:
        span.set_tag_str("vertexai.request.contents.%d.role" % content_idx, str(role))
    parts = getattr(content, "parts", [])
    if not parts:
        span.set_tag_str(
            "vertexai.request.contents.%d.text" % content_idx,
            integration.trunc("[Non-text content object: {}]".format(repr(content))),
        )
        return
    for part_idx, part in enumerate(parts):
        _tag_request_content_part(span, integration, part, part_idx, content_idx)


def _tag_response_part(span, integration, part, part_idx, candidate_idx):
    """Tag the generation span with response part text and function calls."""
    text = part.get("text", "")
    span.set_tag_str(
        "vertexai.response.candidates.%d.content.parts.%d.text" % (candidate_idx, part_idx),
        integration.trunc(str(text)),
    )
    function_call = part.get("function_call", None)
    if not function_call:
        return
    span.set_tag_str(
        "vertexai.response.candidates.%d.content.parts.%d.function_call.name" % (candidate_idx, part_idx),
        integration.trunc(str(function_call.get("name", ""))),
    )
    span.set_tag_str(
        "vertexai.response.candidates.%d.content.parts.%d.function_call.args" % (candidate_idx, part_idx),
        integration.trunc(str(function_call.get("args", {}))),
    )


def tag_request(span, integration, instance, args, kwargs):
    """Tag the generation span with request details.
    Includes capturing generation configuration, system prompt, and messages.
    """
    # instance is either a chat session or a model itself
    model_instance = instance if isinstance(instance, GenerativeModel) else instance._model
    contents = get_argument_value(args, kwargs, 0, "contents")
    history = getattr(instance, "_history", [])
    if history:
        if isinstance(contents, list):
            contents = history + contents
        if isinstance(contents, Part) or isinstance(contents, str) or isinstance(contents, dict):
            contents = history + [contents]
    generation_config_dict = get_generation_config_from_model(model_instance, kwargs)
    system_instructions = get_system_instruction_texts_from_model(model_instance)
    stream = kwargs.get("stream", None)

    if generation_config_dict is not None:
        for k, v in generation_config_dict.items():
            span.set_tag_str("vertexai.request.generation_config.%s" % k, str(v))

    if stream:
        span.set_tag("vertexai.request.stream", True)

    if not integration.is_pc_sampled_span(span):
        return

    for idx, text in enumerate(system_instructions):
        span.set_tag_str(
            "vertexai.request.system_instruction.%d.text" % idx,
            integration.trunc(str(text)),
        )

    if isinstance(contents, str) or isinstance(contents, dict):
        span.set_tag_str("vertexai.request.contents.0.text", integration.trunc(str(contents)))
        return
    elif isinstance(contents, Part):
        _tag_request_content_part(span, integration, contents, 0, 0)
        return
    elif not isinstance(contents, list):
        return
    for content_idx, content in enumerate(contents):
        _tag_request_content(span, integration, content, content_idx)


def tag_response(span, generations, integration):
    """Tag the generation span with response details.
    Includes capturing generation text, roles, finish reasons, and token counts.
    """
    generations_dict = generations.to_dict()
    for candidate_idx, candidate in enumerate(generations_dict.get("candidates", [])):
        finish_reason = candidate.get("finish_reason", None)
        if finish_reason:
            span.set_tag_str("vertexai.response.candidates.%d.finish_reason" % candidate_idx, str(finish_reason))
        candidate_content = candidate.get("content", {})
        role = candidate_content.get("role", "")
        span.set_tag_str("vertexai.response.candidates.%d.content.role" % candidate_idx, str(role))
        if not integration.is_pc_sampled_span(span):
            continue
        parts = candidate_content.get("parts", [])
        for part_idx, part in enumerate(parts):
            _tag_response_part(span, integration, part, part_idx, candidate_idx)

    token_counts = generations_dict.get("usage_metadata", None)
    if not token_counts:
        return
    span.set_metric("vertexai.response.usage.prompt_tokens", token_counts.get("prompt_token_count", 0))
    span.set_metric("vertexai.response.usage.completion_tokens", token_counts.get("candidates_token_count", 0))
    span.set_metric("vertexai.response.usage.total_tokens", token_counts.get("total_token_count", 0))
