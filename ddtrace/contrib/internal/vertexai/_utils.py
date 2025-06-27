import sys

from vertexai.generative_models import GenerativeModel
from vertexai.generative_models import Part

from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._integrations.base_stream_handler import AsyncStreamHandler
from ddtrace.llmobs._integrations.base_stream_handler import StreamHandler
from ddtrace.llmobs._integrations.utils import get_generation_config_google
from ddtrace.llmobs._integrations.utils import get_system_instructions_from_google_model
from ddtrace.llmobs._integrations.utils import tag_request_content_part_google
from ddtrace.llmobs._integrations.utils import tag_response_part_google
from ddtrace.llmobs._utils import _get_attr

class BaseVertexAIStreamHandler:
    def initialize_chunk_storage(self):
        return []
    
    def _process_chunk(self, chunk):
        if not self.options.get("is_chat", False) or not self.chunks:
            self.chunks.append(chunk)

    def finalize_stream(self, exception=None):
        tag_stream_response(self.primary_span, self.chunks, self.integration)
        if self.integration.is_pc_sampled_llmobs(self.primary_span):
            self.request_kwargs["instance"] = self.options.get("model_instance", None)
            self.request_kwargs["history"] = self.options.get("history", None)
            self.integration.llmobs_set_tags(
                self.primary_span, args=self.request_args, kwargs=self.request_kwargs, response=self.chunks
            )
        self.primary_span.finish()

class VertexAIStreamHandler(BaseVertexAIStreamHandler, StreamHandler):
    def process_chunk(self, chunk, iterator=None):
        self._process_chunk(chunk)

class VertexAIAsyncStreamHandler(BaseVertexAIStreamHandler, AsyncStreamHandler):
    async def process_chunk(self, chunk, iterator=None):
        self._process_chunk(chunk)

def extract_info_from_parts(parts):
    """Return concatenated text from parts and function calls."""
    concatenated_text = ""
    function_calls = []
    for part in parts:
        text = _get_attr(part, "text", "")
        concatenated_text += text
        function_call = _get_attr(part, "function_call", None)
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
            _get_attr(function_call, "name", ""),
        )
        span.set_tag_str(
            "vertexai.response.candidates.%d.content.parts.%d.function_calls.%d.function_call.args" % (0, 0, idx),
            integration.trunc(str(_get_attr(function_call, "args", ""))),
        )


def tag_stream_response(span, chunks, integration):
    all_parts = []
    role = ""
    for chunk in chunks:
        candidates = _get_attr(chunk, "candidates", [])
        for candidate_idx, candidate in enumerate(candidates):
            finish_reason = _get_attr(candidate, "finish_reason", None)
            if finish_reason:
                span.set_tag_str(
                    "vertexai.response.candidates.%d.finish_reason" % (candidate_idx),
                    _get_attr(finish_reason, "name", ""),
                )
            candidate_content = _get_attr(candidate, "content", {})
            role = role or _get_attr(candidate_content, "role", "")
            if not integration.is_pc_sampled_span(span):
                continue
            parts = _get_attr(candidate_content, "parts", [])
            all_parts.extend(parts)
        token_counts = _get_attr(chunk, "usage_metadata", None)
        if not token_counts:
            continue
        span.set_metric("vertexai.response.usage.prompt_tokens", _get_attr(token_counts, "prompt_token_count", 0))
        span.set_metric(
            "vertexai.response.usage.completion_tokens", _get_attr(token_counts, "candidates_token_count", 0)
        )
        span.set_metric("vertexai.response.usage.total_tokens", _get_attr(token_counts, "total_token_count", 0))
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
            span.set_tag_str("vertexai.request.contents.%d.role" % content_idx, role)
        parts = content.get("parts", [])
        for part_idx, part in enumerate(parts):
            tag_request_content_part_google("vertexai", span, integration, part, part_idx, content_idx)
        return
    if isinstance(content, Part):
        tag_request_content_part_google("vertexai", span, integration, content, 0, content_idx)
        return
    role = _get_attr(content, "role", "")
    if role:
        span.set_tag_str("vertexai.request.contents.%d.role" % content_idx, str(role))
    parts = _get_attr(content, "parts", [])
    if not parts:
        span.set_tag_str(
            "vertexai.request.contents.%d.text" % content_idx,
            integration.trunc("[Non-text content object: {}]".format(repr(content))),
        )
        return
    for part_idx, part in enumerate(parts):
        tag_request_content_part_google("vertexai", span, integration, part, part_idx, content_idx)


def tag_request(span, integration, instance, args, kwargs, is_chat):
    """Tag the generation span with request details.
    Includes capturing generation configuration, system prompt, and messages.
    """
    # instance is either a chat session or a model itself
    model_instance = instance if isinstance(instance, GenerativeModel) else instance._model
    contents = get_argument_value(args, kwargs, 0, "content" if is_chat else "contents")
    history = _get_attr(instance, "_history", [])
    if history:
        if isinstance(contents, list):
            contents = history + contents
        if isinstance(contents, Part) or isinstance(contents, str) or isinstance(contents, dict):
            contents = history + [contents]
    generation_config = get_generation_config_google(model_instance, kwargs)
    generation_config_dict = None
    if generation_config is not None:
        generation_config_dict = (
            generation_config if isinstance(generation_config, dict) else generation_config.to_dict()
        )
    system_instructions = get_system_instructions_from_google_model(model_instance)
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

    if isinstance(contents, str):
        span.set_tag_str("vertexai.request.contents.0.text", integration.trunc(str(contents)))
        return
    elif isinstance(contents, Part):
        tag_request_content_part_google("vertexai", span, integration, contents, 0, 0)
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
    candidates = generations_dict.get("candidates", [])
    for candidate_idx, candidate in enumerate(candidates):
        finish_reason = _get_attr(candidate, "finish_reason", None)
        if finish_reason:
            span.set_tag_str("vertexai.response.candidates.%d.finish_reason" % candidate_idx, finish_reason)
        candidate_content = _get_attr(candidate, "content", None)
        role = _get_attr(candidate_content, "role", "")
        span.set_tag_str("vertexai.response.candidates.%d.content.role" % candidate_idx, str(role))
        if not integration.is_pc_sampled_span(span):
            continue
        parts = _get_attr(candidate_content, "parts", [])
        for part_idx, part in enumerate(parts):
            tag_response_part_google("vertexai", span, integration, part, part_idx, candidate_idx)

    token_counts = generations_dict.get("usage_metadata", None)
    if not token_counts:
        return
    span.set_metric("vertexai.response.usage.prompt_tokens", _get_attr(token_counts, "prompt_token_count", 0))
    span.set_metric("vertexai.response.usage.completion_tokens", _get_attr(token_counts, "candidates_token_count", 0))
    span.set_metric("vertexai.response.usage.total_tokens", _get_attr(token_counts, "total_token_count", 0))
