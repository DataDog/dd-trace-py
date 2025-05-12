from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._constants import INPUT_DOCUMENTS
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._integrations.utils import get_llmobs_metrics_tags
from ddtrace.llmobs._integrations.utils import is_openai_default_base_url
from ddtrace.llmobs._integrations.utils import openai_set_meta_tags_from_chat
from ddtrace.llmobs._integrations.utils import openai_set_meta_tags_from_completion
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
        base_url = kwargs.get("base_url", None)
        submit_to_llmobs = self.is_default_base_url(str(base_url) if base_url else None) and (
            operation_id.endswith("Completion") or operation_id == "createEmbedding"
        )
        return super().trace(pin, operation_id, submit_to_llmobs, **kwargs)

    def _set_base_span_tags(self, span: Span, **kwargs) -> None:
        span.set_tag_str(COMPONENT, self.integration_config.integration_name)
        if self._user_api_key is not None:
            span.set_tag_str("openai.user.api_key", self._user_api_key)

        # Do these dynamically as openai users can set these at any point
        # not necessarily before patch() time.
        # organization_id is only returned by a few endpoints, grab it when we can.
        if parse_version(self._openai.version.VERSION) >= (1, 0, 0):
            source = self._client
            base_attrs: Tuple[str, ...] = ("base_url", "organization")
        else:
            source = self._openai
            base_attrs = ("api_base", "api_version", "api_type", "organization")
        for attr in base_attrs:
            v = getattr(source, attr, None)
            if v is not None:
                if attr == "organization":
                    span.set_tag_str("openai.organization.id", v or "")
                else:
                    span.set_tag_str("openai.%s" % attr, str(v))
        client = "OpenAI"
        if self._is_provider(span, "azure"):
            client = "AzureOpenAI"
        elif self._is_provider(span, "deepseek"):
            client = "Deepseek"
        span.set_tag_str("openai.request.client", client)

    @staticmethod
    def _is_provider(span, provider):
        """Check if the traced operation is from the given provider."""
        base_url = span.get_tag("openai.base_url") or span.get_tag("openai.api_base")
        if not base_url or not isinstance(base_url, str):
            return False
        return provider.lower() in base_url.lower()

    def record_usage(self, span: Span, usage: Dict[str, Any]) -> None:
        if not usage:
            return
        for token_type in ("prompt", "completion", "output", "input", "total"):
            num_tokens = getattr(usage, token_type + "_tokens", None)
            if not num_tokens:
                continue
            span.set_metric("openai.response.usage.%s_tokens" % token_type, num_tokens)
        for token_type in ("input", "output", "total"):
            num_tokens = getattr(usage, token_type + "_tokens", None)
            if not num_tokens:
                continue
            span.set_metric("openai.response.usage.%s_tokens" % token_type, num_tokens)

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",  # oneof "completion", "chat", "embedding"
    ) -> None:
        """Sets meta tags and metrics for span events to be sent to LLMObs."""
        span_kind = "embedding" if operation == "embedding" else "llm"
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
        elif operation == "responses":
            self._llmobs_set_meta_tags_from_responses(span, kwargs, response)
        metrics = self._extract_llmobs_metrics_tags(span, response)
        print(f"DEBUG: _llmobs_set_tags - Setting context items: span_kind={span_kind}, model_name={model_name}, model_provider={model_provider}")
        span._set_ctx_items(
            {SPAN_KIND: span_kind, MODEL_NAME: model_name or "", MODEL_PROVIDER: model_provider, METRICS: metrics}
        )
        print(f"DEBUG: _llmobs_set_tags - After setting context items: span_kind={span._get_ctx_item(SPAN_KIND)}, input_messages={span._get_ctx_item(INPUT_MESSAGES)}")

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
        if span.error:
            return
        if encoding_format == "float":
            embedding_dim = len(resp.data[0].embedding)
            span._set_ctx_item(
                OUTPUT_VALUE, "[{} embedding(s) returned with size {}]".format(len(resp.data), embedding_dim)
            )
            return
        span._set_ctx_item(OUTPUT_VALUE, "[{} embedding(s) returned]".format(len(resp.data)))

    @staticmethod
    def _extract_llmobs_metrics_tags(span: Span, resp: Any) -> Dict[str, Any]:
        """Extract metrics from a chat/completion and set them as a temporary "_ml_obs.metrics" tag."""
        token_usage = _get_attr(resp, "usage", None)
        if token_usage is not None:
            prompt_tokens = _get_attr(token_usage, "prompt_tokens", 0)
            completion_tokens = _get_attr(token_usage, "completion_tokens", 0)
            input_tokens = _get_attr(token_usage, "input_tokens", 0)
            output_tokens = _get_attr(token_usage, "output_tokens", 0)

            input_tokens_value = prompt_tokens if prompt_tokens != 0 else input_tokens
            output_tokens_value = completion_tokens if completion_tokens != 0 else output_tokens
            
            return {
                INPUT_TOKENS_METRIC_KEY: input_tokens_value,
                OUTPUT_TOKENS_METRIC_KEY: output_tokens_value,
                TOTAL_TOKENS_METRIC_KEY: input_tokens_value + output_tokens_value,
            }
        return get_llmobs_metrics_tags("openai", span)

    def is_default_base_url(self, base_url: Optional[str] = None) -> bool:
        return is_openai_default_base_url(base_url)

    @staticmethod
    def _llmobs_set_meta_tags_from_responses(span: Span, kwargs: Dict[str, Any], messages: Optional[Any]) -> None:
        """Extract prompt/response tags from a responses and set them as temporary "_ml_obs.meta.*" tags."""
        print(f"DEBUG: _llmobs_set_meta_tags_from_responses - span id = {id(span)}")
        print(f"DEBUG: _llmobs_set_meta_tags_from_responses - span._store = {span._store}")
        response_input = span._get_ctx_item("llmobs.response.input")
        # print(f"DEBUG: Got response_input from context = {response_input}")

        input_messages = []
        if response_input:
            if isinstance(response_input, str):
                # Handle case where response_input is a text string
                input_messages = [{"content": response_input, "role": ""}]
            elif isinstance(response_input, dict):
                input_messages = [
                    {
                        "content": response_input.get("content", ""),
                        "role": response_input.get("role", ""),
                        "type": response_input.get("type", ""),
                    }
                ]
            elif isinstance(response_input, list):
                for m in response_input:
                    if isinstance(m, dict):
                        input_messages.append({"content": m.get("content", ""), "role": m.get("role", "")})
                    else:
                        input_messages.append({"content": str(m), "role": ""})
            # print(f"DEBUG: Processed input_messages = {input_messages}")

        parameters = {k: v for k, v in kwargs.items() if k not in ("model", "input", "tools")}
        # Only set in context, not as tag
        span._set_ctx_items({INPUT_MESSAGES: input_messages, METADATA: parameters})


        if span.error or not messages:
            span._set_ctx_item(OUTPUT_MESSAGES, [{"content": ""}])
            return

        response_output = span._get_ctx_item("llmobs.response.output")
        # print(f"response_output = {response_output}")

        output_messages = []
        if response_output:
            if isinstance(response_output, list):
                # Handle streaming response
                for item in response_output:
                    if hasattr(item, "type") and item.type == "message":
                        role = getattr(item, "role", "")
                        if hasattr(item, "content"):
                            content = item.content
                            if isinstance(content, list):
                                for content_item in content:
                                    if hasattr(content_item, "text"):
                                        output_messages.append({"content": content_item.text, "role": role})
                            else:
                                text = getattr(content, "text", "") if hasattr(content, "text") else str(content)
                                output_messages.append({"content": text, "role": role})
            else:
                # Handle non-streaming response
                if hasattr(response_output, "type") and response_output.type == "message":
                    role = getattr(response_output, "role", "")
                    if hasattr(response_output, "content"):
                        content = response_output.content
                        if isinstance(content, list):
                            for content_item in content:
                                if hasattr(content_item, "text"):
                                    output_messages.append({"content": content_item.text, "role": role})
                        else:
                            text = getattr(content, "text", "") if hasattr(content, "text") else str(content)
                            output_messages.append({"content": text, "role": role})
                else:
                    output_messages = [{"content": str(response_output), "role": ""}]
        # print(f"output_messages = {output_messages}")
        span._set_ctx_item(OUTPUT_MESSAGES, output_messages)
        span.set_tag("_ml_obs.meta.output.messages", output_messages)
        # print(f"DEBUG: Context items after setting output = {span._store}")
        # print(f"DEBUG: Span tags after setting output = {span._meta}")
