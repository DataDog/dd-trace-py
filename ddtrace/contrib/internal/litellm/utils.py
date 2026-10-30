from collections import defaultdict
from dataclasses import FrozenInstanceError

from ddtrace._monkey import _get_patched_modules
from ddtrace.contrib.internal.litellm._constants import TAG_RESPONSE_MODEL
from ddtrace.contrib.internal.litellm._constants import TEXT_COMPLETION_OPERATIONS
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env
from ddtrace.internal.span_bus import span_from_context
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.openai_streaming import openai_construct_completion_from_streamed_chunks
from ddtrace.internal.utils.openai_streaming import openai_construct_message_from_streamed_chunks
from ddtrace.internal.utils.streaming import AsyncStreamHandler
from ddtrace.internal.utils.streaming import StreamHandler


log = get_logger(__name__)

# Maps requested model name to (parsed model name, provider), filled in as litellm resolves providers.
model_map: dict[str, tuple[str, str]] = {}


def extract_host_tag(kwargs):
    if "host" in (kwargs.get("metadata") or {}).get("headers", {}):
        return kwargs["metadata"]["headers"]["host"]
    return None


def is_completion_operation(operation: str) -> bool:
    return operation in TEXT_COMPLETION_OPERATIONS


def _openai_is_patched():
    return "openai" in _get_patched_modules()


def has_downstream_openai_span(kwargs, model=None):
    """
    Determine whether an LLM span will be submitted for the given request from outside the LiteLLM integration.

    Currently, this only applies to the OpenAI integration when the following conditions are met:
        - request is not streamed
        - model provider is OpenAI/AzureOpenAI
        - the OpenAI integration is enabled
    """
    stream = kwargs.get("stream", False)
    model_lower = model.lower() if model else ""
    # Detect litellm proxy routing — the OpenAI integration never fires when requests go through a proxy.
    # Three mechanisms exist (v1.72.1+ for use_litellm_proxy):
    #   1. model prefix:        model="litellm_proxy/<model>"
    #   2. per-call kwarg:      use_litellm_proxy=True
    #   3. module-level flag:   litellm.use_litellm_proxy = True  /  USE_LITELLM_PROXY env var
    if model_lower.startswith("litellm_proxy/"):
        return False
    if kwargs.get("use_litellm_proxy"):
        return False
    import litellm

    if getattr(litellm, "use_litellm_proxy", False) or env.get("USE_LITELLM_PROXY", "").lower() == "true":
        return False
    # OpenRouter routes through litellm's own HTTP handler, not the OpenAI SDK, so there's no
    # downstream OpenAI span to defer to.
    if model_lower.startswith("openrouter/"):
        return False
    # best effort attempt to check if Open AI or Azure since model_provider is unknown until request completes
    is_openai_model = any(prefix in model_lower for prefix in ("gpt", "openai", "azure"))
    return is_openai_model and not stream and _openai_is_patched()


class BaseLiteLLMStreamHandler:
    """Mixed into StreamHandler/AsyncStreamHandler, which provide chunks and options.

    A Router call streams the response of the litellm call it makes, so one stream can
    finish several requests. Each request is an execution context whose ended event this
    handler dispatches once the stream is consumed or closed.
    """

    def __init__(self, ctx):
        super().__init__(None, span_from_context(ctx), ctx.event.request_args, ctx.event.request_kwargs)
        self.contexts = [ctx]

    def initialize_chunk_storage(self):
        return defaultdict(list)

    def add_context(self, ctx):
        self.contexts.append(ctx)

    def _process_chunk(self, chunk, iterator=None):
        # Capture the first non-empty response model. Only emitted as a span tag for azure/azure_text
        # providers (in finalize_stream), where the request model is an arbitrary deployment name.
        if not getattr(self, "response_model", None):
            chunk_model = getattr(chunk, "model", None)
            if chunk_model:
                try:
                    self.response_model = chunk_model
                except (AttributeError, FrozenInstanceError):
                    pass
        for choice in getattr(chunk, "choices", []):
            choice_index = getattr(choice, "index", 0)
            self.chunks[choice_index].append(choice)
        if getattr(chunk, "usage", None):
            self.chunks[0].insert(0, chunk)

    def finalize_stream(self, exception=None):
        # handle_exception already recorded a stream error on the primary span.
        response = self._process_finished_stream(self.contexts[0].event.operation)
        response_model = getattr(self, "response_model", None)
        for ctx in self.contexts:
            event = ctx.event
            if response_model:
                # Resolve the requested model from args or kwargs (callers may pass it positionally,
                # e.g. litellm.completion("azure/<deployment>", ...)) so provider detection works.
                requested_model = (
                    get_argument_value(event.request_args, event.request_kwargs, 0, "model", optional=True) or ""
                )
                _, provider = model_map.get(requested_model, ("", ""))
                if provider in ("azure", "azure_text"):
                    span_from_context(ctx).set_tag(TAG_RESPONSE_MODEL, response_model)
            event.response = response
            ctx.dispatch_ended_event()

    def _process_finished_stream(self, operation):
        try:
            if is_completion_operation(operation):
                return [openai_construct_completion_from_streamed_chunks(choice) for choice in self.chunks.values()]
            return [openai_construct_message_from_streamed_chunks(choice) for choice in self.chunks.values()]
        except Exception:
            log.warning("Error processing streamed completion/chat response.", exc_info=True)
            return None


class LiteLLMStreamHandler(BaseLiteLLMStreamHandler, StreamHandler):
    def process_chunk(self, chunk, iterator=None):
        self._process_chunk(chunk, iterator)


class LiteLLMAsyncStreamHandler(BaseLiteLLMStreamHandler, AsyncStreamHandler):
    async def process_chunk(self, chunk, iterator=None):
        self._process_chunk(chunk, iterator)
