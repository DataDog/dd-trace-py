import sys

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
            self._kwargs["instance"] = self._model_instance
            self._kwargs["history"] = self._history
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
            self._kwargs["instance"] = self._model_instance
            self._kwargs["history"] = self._history
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
