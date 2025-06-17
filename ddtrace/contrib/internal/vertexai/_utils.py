import sys

from vertexai.generative_models import GenerativeModel
from vertexai.generative_models import Part

from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._integrations.utils import get_generation_config_google
from ddtrace.llmobs._integrations.utils import get_system_instructions_from_google_model
from ddtrace.llmobs._integrations.utils import tag_request_content_part_google
from ddtrace.llmobs._integrations.utils import tag_response_part_google
from ddtrace.llmobs._utils import _get_attr


class BaseTracedVertexAIStreamResponse:
    def __init__(self, generator, model_instance, integration, span, args, kwargs, is_chat, history):
        self._generator = generator
        self._model_instance = model_instance
        self._dd_integration = integration
        self._dd_span = span
        self._args = args
        self._kwargs = kwargs
        self.is_chat = is_chat
        self._chunks = []
        self._history = history


class TracedVertexAIStreamResponse(BaseTracedVertexAIStreamResponse):
    def __enter__(self):
        self._generator.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._generator.__exit__(exc_type, exc_val, exc_tb)

    def __iter__(self):
        try:
            for chunk in self._generator.__iter__():
                # only keep track of the first chunk for chat messages since
                # it is modified during the streaming process
                if not self.is_chat or not self._chunks:
                    self._chunks.append(chunk)
                yield chunk
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            raise
        finally:
            metrics = extract_metrics_from_response(self._chunks[0])
            if self._dd_integration.is_pc_sampled_llmobs(self._dd_span):
                self._kwargs["instance"] = self._model_instance
                self._kwargs["history"] = self._history
                self._kwargs["metrics"] = metrics
                self._dd_integration.llmobs_set_tags(
                    self._dd_span, args=self._args, kwargs=self._kwargs, response=self._chunks
                )
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
                # only keep track of the first chunk for chat messages since
                # it is modified during the streaming process
                if not self.is_chat or not self._chunks:
                    self._chunks.append(chunk)
                yield chunk
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            raise
        finally:
            metrics = extract_metrics_from_response(self._chunks[0])
            if self._dd_integration.is_pc_sampled_llmobs(self._dd_span):
                self._kwargs["instance"] = self._model_instance
                self._kwargs["history"] = self._history
                self._kwargs["metrics"] = metrics
                self._dd_integration.llmobs_set_tags(
                    self._dd_span, args=self._args, kwargs=self._kwargs, response=self._chunks
                )
            self._dd_span.finish()


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


def tag_request(span, integration, instance, args, kwargs, is_chat):
    """Tag the generation span with request details.
    Includes capturing generation configuration, system prompt, and messages.
    """
    # instance is either a chat session or a model itself
    stream = kwargs.get("stream", None)

    if stream:
        span.set_tag("vertexai.request.stream", True)

def extract_metrics_from_response(generations):
    """Extract metrics from the response."""
    generations_dict = generations.to_dict()

    token_counts = generations_dict.get("usage_metadata", None)
    if not token_counts:
        return

    metrics = {
        "prompt_tokens": _get_attr(token_counts, "prompt_token_count", 0),
        "completion_tokens": _get_attr(token_counts, "candidates_token_count", 0),
        "total_tokens": _get_attr(token_counts, "total_token_count", 0),
    }

    return metrics
