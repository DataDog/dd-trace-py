import sys

def get_system_instruction_parts(instance):
    """
    Assumes that the system instruction is provided as []Part
    """
    return getattr(instance, "_system_instruction", None)

def get_generation_config_dict(instance, kwargs):
    """
    The generation config can be defined on the model instance or 
    as a kwarg to generate_content. Therefore, try to extract this information
    from the kwargs and otherwise default to checking the model instance attribute.
    """
    generation_config_arg = kwargs.get("generation_config", {})
    if generation_config_arg != {}:
        return generation_config_arg if isinstance(generation_config_arg, dict) else generation_config_arg.to_dict()
    generation_config_attr = getattr(instance, "_generation_config", {})
    return generation_config_attr if isinstance(generation_config_attr, dict) else generation_config_attr.to_dict()

def get_concatenated_parts(parts):
    """Concatenate all parts from a generation into a single text."""
    concatenated_text = ""
    for part in parts:
        concatenated_text += part.text
    return concatenated_text

def tag_stream_response(tag_prefix, span, chunks, integration, instance):
    content = ""
    role = ""
    for chunk in chunks:
        for candidate_idx, candidate in enumerate(chunk.candidates):
            finish_reason = candidate.finish_reason
            if finish_reason:
                span.set_tag_str(
                    "%s.response.candidates.%d.finish_reason" % (tag_prefix, candidate_idx), str(finish_reason)
                )
            candidate_content = candidate.content
            role = candidate_content.role
            span.set_tag_str("%s.response.candidates.%d.content.role" % (tag_prefix, candidate_idx), str(role))
            if not integration.is_pc_sampled_span(span):
                continue
            content += get_concatenated_parts(candidate_content.parts)
        token_counts = chunk.usage_metadata
        if not token_counts:
            continue
        span.set_metric("%s.response.usage.prompt_tokens" % tag_prefix, token_counts.prompt_token_count)
        span.set_metric(
            "%s.response.usage.completion_tokens" % tag_prefix, token_counts.candidates_token_count
        )
        span.set_metric("%s.response.usage.total_tokens" % tag_prefix, token_counts.total_token_count)
    span.set_tag_str("%s.response.candidates.0.content.parts.0.text" % tag_prefix, content)

class BaseTracedVertexAIStreamResponse:
    def __init__(self, generator, instance, integration, span, args, kwargs, on_span_finish):
        self._generator = generator
        self._model_instance = instance
        self._dd_integration = integration
        self._dd_span = span
        self._args = args
        self._kwargs = kwargs
        self._on_span_finish = on_span_finish
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
                self._chunks.append(chunk)
                yield chunk
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            raise
        else:
            tag_stream_response("vertexai", self._dd_span, self._chunks, self._dd_integration, self._model_instance)
        finally:
            self._kwargs["instance"] = self._model_instance
            self._dd_span.finish()

