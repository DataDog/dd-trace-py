import json
import os
import sys

import google.generativeai as genai

from ddtrace import config
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._integrations import GeminiIntegration
from ddtrace.pin import Pin


config._add(
    "genai",
    {
        "span_prompt_completion_sample_rate": float(os.getenv("DD_GOOGLE_GENAI_SPAN_PROMPT_COMPLETION_SAMPLE_RATE", 1.0)),
        "span_char_limit": int(os.getenv("DD_GOOGLE_GENAI_SPAN_CHAR_LIMIT", 128)),
    },
)


def get_version():
    # type: () -> str
    return getattr(genai, "__version__", "")


def _tag_request(span, integration, instance, args, kwargs):
    """Tag the generation span with request details.
    Includes capturing generation configuration, system prompts, message contents, and function call/responses.
    """
    contents = get_argument_value(args, kwargs, 0, "contents")
    generation_config = kwargs.get("generation_config", {})
    system_instruction = getattr(instance, "_system_instruction", "")

    generation_config_dict = None
    if isinstance(generation_config, dict):
        generation_config_dict = generation_config
    elif generation_config is not None:
        generation_config_dict = generation_config.__dict__
    if generation_config_dict is not None:
        for k, v in generation_config_dict.items():
            span.set_tag_str("genai.request.generation_config.%s" % k, str(v))

    if not integration.is_pc_sampled_span(span):
        return

    span.set_tag("genai.request.system_instruction", integration.trunc(system_instruction))
    if isinstance(contents, str):
        span.set_tag_str("genai.request.contents.0.text", integration.trunc(contents))
    elif isinstance(contents, dict):
        span.set_tag_str("genai.request.contents.0.text", integration.trunc(str(contents)))
    elif isinstance(contents, list):
        for content_idx, content in enumerate(contents):
            if isinstance(content, str):
                span.set_tag_str("genai.request.contents.%d.text" % content_idx, integration.trunc(content))
                continue
            if isinstance(content, dict):
                role = content.get("role", "")
                if role:
                    span.set_tag_str("genai.request.contents.%d.role" % content_idx, str(content.get("role", "")))
                span.set_tag_str(
                    "genai.request.contents.%d.parts" % content_idx, integration.trunc(str(content.get("parts", [])))
                )
                continue
            role = getattr(content, "role", "")
            if role:
                span.set_tag_str("genai.request.contents.%d.role" % content_idx, str(role))
            parts = getattr(content, "parts", [])
            for part_idx, part in enumerate(parts):
                text = getattr(part, "text", "")
                span.set_tag_str(
                    "genai.request.contents.%d.parts.%d.text" % (content_idx, part_idx), integration.trunc(str(text))
                )
                function_call = getattr(part, "function_call", None)
                if function_call:
                    function_call_dict = type(function_call).to_dict(function_call)
                    span.set_tag_str(
                        "genai.request.contents.%d.parts.%d.function_call.name" % (content_idx, part_idx),
                        integration.trunc(str(function_call_dict.get("name", "")))
                    )
                    span.set_tag_str(
                        "genai.request.contents.%d.parts.%d.function_call.args" % (content_idx, part_idx),
                        integration.trunc(str(function_call_dict.get("args", {})))
                    )
                function_response = getattr(part, "function_response", None)
                if function_response:
                    function_response_dict = type(function_response).to_dict(function_response)
                    span.set_tag_str(
                        "genai.request.contents.%d.parts.%d.function_response.name" % (content_idx, part_idx),
                        str(function_response_dict.get("name", ""))
                    )
                    span.set_tag_str(
                        "genai.request.contents.%d.parts.%d.function_response.response" % (content_idx, part_idx),
                        integration.trunc(str(function_response_dict.get("response", {})))
                    )


def _tag_response(span, generations, integration):
    """Tag the generation span with response details.
    Includes capturing generation text, roles, finish reasons, and token counts.
    """
    generations_dict = generations.to_dict()
    for idx, candidate in enumerate(generations_dict.get("candidates", [])):
        finish_reason = candidate.get("finish_reason", None)
        if finish_reason:
            span.set_tag_str("genai.response.candidates.%d.finish_reason" % idx, str(finish_reason))
        candidate_content = candidate.get("content", {})
        role = candidate_content.get("role", "")
        span.set_tag_str("genai.response.candidates.%d.content.role" % idx, str(role))
        if integration.is_pc_sampled_span(span):
            parts = candidate_content.get("parts", [])
            for part_idx, part in enumerate(parts):
                text = part.get("text", "")
                span.set_tag_str(
                    "genai.response.candidates.%d.content.parts.%d.text" % (idx, part_idx),
                    integration.trunc(str(text)),
                )
                function_call = part.get("function_call", None)
                if function_call:
                    span.set_tag_str(
                        "genai.response.candidates.%d.content.parts.%d.function_call.name" % (idx, part_idx),
                        integration.trunc(function_call.get("name", ""))
                    )
                    span.set_tag_str(
                        "genai.response.candidates.%d.content.parts.%d.function_call.args" % (idx, part_idx),
                        integration.trunc(str(function_call.get("args", {})))
                    )
    token_counts = generations_dict.get("usage_metadata", None)
    if token_counts:
        span.set_metric("genai.response.usage.prompt_tokens", token_counts.get("prompt_token_count", 0))
        span.set_metric("genai.response.usage.completion_tokens", token_counts.get("candidates_token_count", 0))
        span.set_metric("genai.response.usage.total_tokens", token_counts.get("total_token_count", 0))


@with_traced_module
def traced_generate(genai, pin, func, instance, args, kwargs):
    integration = genai._datadog_integration
    stream = kwargs.get("stream", False)
    generations = None
    span = integration.trace(
        pin, "%s.%s" % (instance.__class__.__name__, func.__name__), submit_to_llmobs=True, provider="google",
    )
    try:
        _tag_request(span, integration, instance, args, kwargs)
        generations = func(*args, **kwargs)
        if stream:
            return # TODO: handle streams
        else:
            _tag_response(span, generations, integration)

    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        if integration.is_pc_sampled_llmobs(span):
            integration.set_llmobs_tags(span, generations)
        # streamed spans will be finished separately once the stream generator is exhausted
        if span.error or not stream:
            span.finish()
    return generations


@with_traced_module
async def traced_agenerate(genai, pin, func, instance, args, kwargs):
    integration = genai._datadog_integration
    stream = kwargs.get("stream", False)
    generations = None
    span = integration.trace(
        pin, "%s.%s" % (instance.__class__.__name__, func.__name__), submit_to_llmobs=True, provider="google",
    )
    try:
        _tag_request(span, integration, instance, args, kwargs)
        generations = await func(*args, **kwargs)
        if stream:
            return # TODO: handle streams
        else:
            _tag_response(span, generations, integration)

    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        if integration.is_pc_sampled_llmobs(span):
            integration.set_llmobs_tags(span, generations)
        # streamed spans will be finished separately once the stream generator is exhausted
        if span.error or not stream:
            span.finish()
    return generations


def patch():
    if getattr(genai, "_datadog_patch", False):
        return

    genai._datadog_patch = True

    Pin().onto(genai)
    integration = GeminiIntegration(integration_config=config.genai)
    genai._datadog_integration = integration

    wrap("google.generativeai", "GenerativeModel.generate_content", traced_generate(genai))
    wrap("google.generativeai", "GenerativeModel.generate_content_async", traced_agenerate(genai))


def unpatch():
    if not getattr(genai, "_datadog_patch", False):
        return

    genai._datadog_patch = False

    unwrap(genai.GenerativeModel, "generate_content")
    unwrap(genai.GenerativeModel, "generate_content_async")

    delattr(genai, "_datadog_integration")
