from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._constants import CACHE_READ_INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import INPUT_DOCUMENTS
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import PROXY_REQUEST
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._integrations.utils import _compute_completion_tokens
from ddtrace.llmobs._integrations.utils import _compute_prompt_tokens
from ddtrace.llmobs._integrations.utils import openai_set_meta_tags_from_chat
from ddtrace.llmobs._integrations.utils import openai_set_meta_tags_from_completion
from ddtrace.llmobs._integrations.utils import openai_set_meta_tags_from_response
from ddtrace.llmobs._integrations.utils import update_proxy_workflow_input_output_value
from ddtrace.llmobs._utils import _get_attr
from ddtrace.llmobs.utils import Document
from ddtrace.trace import Pin
from ddtrace.trace import Span


class OpenAIIntegration(BaseLLMIntegration):
    _integration_name = "openai"

    def __init__(self, integration_config, openai):
        # FIXME: this currently does not consider if the tracer is configured to
        # use a different hostname. eg. tracer.configure(host="new-hostname")
        # Ideally the metrics client should live on the tracer or some other core
        # object that is strongly linked with configuration.
        super().__init__(integration_config)
        self._openai = openai
        self._user_api_key = None
        self._client = None
        if self._openai.api_key is not None:
            self.user_api_key = self._openai.api_key

    @property
    def user_api_key(self) -> Optional[str]:
        """Get a representation of the user API key for tagging."""
        return self._user_api_key

    @user_api_key.setter
    def user_api_key(self, value: str) -> None:
        # Match the API key representation that OpenAI uses in their UI.
        self._user_api_key = "sk-...%s" % value[-4:]

    def trace(self, pin: Pin, operation_id: str, submit_to_llmobs: bool = False, **kwargs: Dict[str, Any]) -> Span:
        traced_operations = ("createCompletion", "createChatCompletion", "createEmbedding", "createResponse")
        if operation_id in traced_operations:
            submit_to_llmobs = True
        return super().trace(pin, operation_id, submit_to_llmobs, **kwargs)

    def _set_base_span_tags(self, span: Span, **kwargs) -> None:
        span.set_tag_str(COMPONENT, self.integration_config.integration_name)

        client = "OpenAI"
        if self._is_provider(span, "azure"):
            client = "AzureOpenAI"
        elif self._is_provider(span, "deepseek"):
            client = "Deepseek"
        span.set_tag_str("openai.request.provider", client)

    def _is_provider(self, span, provider):
        """Check if the traced operation is from the given provider."""
        base_url = None
        if parse_version(self._openai.version.VERSION) >= (1, 0, 0):
            base_url = getattr(self._client, "_base_url", None)
        else:
            base_url = getattr(self._openai, "api_base", None)
        base_url = str(base_url) if base_url else None
        if not base_url or not isinstance(base_url, str):
            return False
        return provider.lower() in base_url.lower()

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",  # oneof "completion", "chat", "embedding", "response"
    ) -> None:
        """Sets meta tags and metrics for span events to be sent to LLMObs."""
        span_kind = (
            "workflow" if span._get_ctx_item(PROXY_REQUEST) else "embedding" if operation == "embedding" else "llm"
        )
        model_name = span.get_tag("openai.response.model") or span.get_tag("openai.request.model")

        model_provider = "openai"
        if self._is_provider(span, "azure"):
            model_provider = "azure_openai"
        elif self._is_provider(span, "deepseek"):
            model_provider = "deepseek"
        if operation == "completion":
            openai_set_meta_tags_from_completion(span, kwargs, response)
        elif operation == "chat":
            openai_set_meta_tags_from_chat(span, kwargs, response)
        elif operation == "embedding":
            self._llmobs_set_meta_tags_from_embedding(span, kwargs, response)
        elif operation == "response":
            openai_set_meta_tags_from_response(span, kwargs, response)
        update_proxy_workflow_input_output_value(span, span_kind)
        metrics = self._extract_llmobs_metrics_tags(span, response, span_kind, kwargs)
        span._set_ctx_items(
            {SPAN_KIND: span_kind, MODEL_NAME: model_name or "", MODEL_PROVIDER: model_provider, METRICS: metrics}
        )

    @staticmethod
    def _llmobs_set_meta_tags_from_embedding(span: Span, kwargs: Dict[str, Any], resp: Any) -> None:
        """Extract prompt tags from an embedding and set them as temporary "_ml_obs.meta.*" tags."""
        encoding_format = kwargs.get("encoding_format") or "float"
        metadata = {"encoding_format": encoding_format}
        if kwargs.get("dimensions"):
            metadata["dimensions"] = kwargs.get("dimensions")

        embedding_inputs = kwargs.get("input", "")
        if isinstance(embedding_inputs, str) or isinstance(embedding_inputs[0], int):
            embedding_inputs = [embedding_inputs]
        input_documents = []
        for doc in embedding_inputs:
            input_documents.append(Document(text=str(doc)))
        span._set_ctx_items({METADATA: metadata, INPUT_DOCUMENTS: input_documents})
        if span.error or not resp:
            return
        if encoding_format == "float":
            embedding_dim = len(resp.data[0].embedding)
            span._set_ctx_item(
                OUTPUT_VALUE, "[{} embedding(s) returned with size {}]".format(len(resp.data), embedding_dim)
            )
            return
        span._set_ctx_item(OUTPUT_VALUE, "[{} embedding(s) returned]".format(len(resp.data)))

    @staticmethod
    def _extract_llmobs_metrics_tags(
        span: Span, resp: Any, span_kind: str, kwargs: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Extract metrics from a chat/completion and set them as a temporary "_ml_obs.metrics" tag."""
        if span_kind == "workflow":
            return None

        token_usage = None

        # in the streamed responses case, `resp` is a list with `usage` being stored in the first element
        if resp and isinstance(resp, list) and _get_attr(resp[0], "usage", None):
            token_usage = _get_attr(resp[0], "usage", {})
        elif resp and _get_attr(resp, "usage", None):
            token_usage = _get_attr(resp, "usage", {})
        if token_usage is not None:
            prompt_tokens = _get_attr(token_usage, "prompt_tokens", 0)
            completion_tokens = _get_attr(token_usage, "completion_tokens", 0)
            input_tokens = _get_attr(token_usage, "input_tokens", 0)
            output_tokens = _get_attr(token_usage, "output_tokens", 0)

            input_tokens = prompt_tokens or input_tokens
            output_tokens = completion_tokens or output_tokens

            metrics = {
                INPUT_TOKENS_METRIC_KEY: input_tokens,
                OUTPUT_TOKENS_METRIC_KEY: output_tokens,
                TOTAL_TOKENS_METRIC_KEY: input_tokens + output_tokens,
            }
            # Chat completions returns `prompt_tokens_details` while responses api returns `input_tokens_details`
            prompt_tokens_details = _get_attr(token_usage, "prompt_tokens_details", {}) or _get_attr(
                token_usage, "input_tokens_details", {}
            )
            cached_tokens = _get_attr(prompt_tokens_details, "cached_tokens", None)
            if cached_tokens is not None:
                metrics[CACHE_READ_INPUT_TOKENS_METRIC_KEY] = cached_tokens
            return metrics
        elif kwargs.get("stream") and resp is not None:
            model_name = span.get_tag("openai.response.model") or kwargs.get("model", "")
            _, prompt_tokens = _compute_prompt_tokens(
                model_name, kwargs.get("prompt", None), kwargs.get("messages", None)
            )
            _, completion_tokens = _compute_completion_tokens(resp, model_name)
            total_tokens = prompt_tokens + completion_tokens

            return {
                INPUT_TOKENS_METRIC_KEY: prompt_tokens,
                OUTPUT_TOKENS_METRIC_KEY: completion_tokens,
                TOTAL_TOKENS_METRIC_KEY: total_tokens,
            }
        return None

    def _get_base_url(self, **kwargs: Dict[str, Any]) -> Optional[str]:
        instance = kwargs.get("instance")
        client = getattr(instance, "_client", None)
        base_url = getattr(client, "_base_url", None) if client else None
        return str(base_url) if base_url else None
