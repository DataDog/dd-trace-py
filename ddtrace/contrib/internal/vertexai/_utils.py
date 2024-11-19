import sys

from vertexai.generative_models import GenerativeModel
from vertexai.generative_models import Part

from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._integrations.utils import tag_request_content_part
from ddtrace.llmobs._integrations.utils import tag_response_part


class BaseTracedVertexAIStreamResponse:
    def __init__(self, generator, integration, span, is_chat):
        self._generator = generator
        self._dd_integration = integration
        self._dd_span = span
        self._chunks = []
        self.is_chat = is_chat


class TracedVertexAIStreamResponse(BaseTracedVertexAIStreamResponse):
    def __enter__(self):
        self._generator.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._generator.__exit__(exc_type, exc_val, exc_tb)

    def __iter__(self):
        try:
            for chunk in self._generator.__iter__():
                # only keep track of first chunk for chat messages
                if not self.is_chat or not self._chunks:
                    self._chunks.append(chunk)
                yield chunk
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            raise
        else:
            tag_stream_response(self._dd_span, self._chunks, self._dd_integration)
        finally:
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
                # only keep track of first chunk for chat messages
                if not self.is_chat or not self._chunks:
                    self._chunks.append(chunk)
                yield chunk
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            raise
        else:
            tag_stream_response(self._dd_span, self._chunks, self._dd_integration)
        finally:
            self._dd_span.finish()


def get_system_instruction_texts_from_model(instance):
    """
    Extract system instructions from model and convert to []str for tagging.
    """
    raw_system_instructions = getattr(instance, "_system_instruction", [])
    if isinstance(raw_system_instructions, str):
        return [raw_system_instructions]
    elif isinstance(raw_system_instructions, Part):
        return [raw_system_instructions.text]
    elif not isinstance(raw_system_instructions, list):
        return []

    system_instructions = []
    for elem in raw_system_instructions:
        if isinstance(elem, str):
            system_instructions.append(elem)
        elif isinstance(elem, Part):
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
            if not integration.is_pc_sampled_span(span):
                continue
            all_parts.extend(candidate_content.parts)
        token_counts = chunk.usage_metadata
        if not token_counts:
            continue
        span.set_metric("vertexai.response.usage.prompt_tokens", token_counts.prompt_token_count)
        span.set_metric("vertexai.response.usage.completion_tokens", token_counts.candidates_token_count)
        span.set_metric("vertexai.response.usage.total_tokens", token_counts.total_token_count)
    # streamed responses have only a single candidate, so there is only one role to be tagged
    span.set_tag_str("vertexai.response.candidates.0.content.role", str(role))
    _tag_response_parts(span, integration, all_parts)


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
            tag_request_content_part("vertexai", span, integration, part, part_idx, content_idx)
        return
    if isinstance(content, Part):
        tag_request_content_part("vertexai", span, integration, content, 0, content_idx)
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
        tag_request_content_part("vertexai", span, integration, part, part_idx, content_idx)


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
        tag_request_content_part("vertexai", span, integration, contents, 0, 0)
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
            tag_response_part("vertexai", span, integration, part, part_idx, candidate_idx)

    token_counts = generations_dict.get("usage_metadata", None)
    if not token_counts:
        return
    span.set_metric("vertexai.response.usage.prompt_tokens", token_counts.get("prompt_token_count", 0))
    span.set_metric("vertexai.response.usage.completion_tokens", token_counts.get("candidates_token_count", 0))
    span.set_metric("vertexai.response.usage.total_tokens", token_counts.get("total_token_count", 0))
