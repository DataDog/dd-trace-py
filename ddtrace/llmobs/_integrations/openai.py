import json
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from ddtrace import config
from ddtrace._trace.span import Span
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._constants import INPUT_DOCUMENTS
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._integrations.anthropic import _get_attr
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._utils import safe_json
from ddtrace.llmobs.utils import Document
from ddtrace.pin import Pin


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
        if operation_id.endswith("Completion") or operation_id == "createEmbedding":
            submit_to_llmobs = True
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

    @classmethod
    def _logs_tags(cls, span: Span) -> str:
        tags = (
            "env:%s,version:%s,openai.request.endpoint:%s,openai.request.method:%s,openai.request.model:%s,openai.organization.name:%s,"
            "openai.user.api_key:%s"
            % (  # noqa: E501
                (config.env or ""),
                (config.version or ""),
                (span.get_tag("openai.request.endpoint") or ""),
                (span.get_tag("openai.request.method") or ""),
                (span.get_tag("openai.request.model") or ""),
                (span.get_tag("openai.organization.name") or ""),
                (span.get_tag("openai.user.api_key") or ""),
            )
        )
        return tags

    @classmethod
    def _metrics_tags(cls, span: Span) -> List[str]:
        model_name = span.get_tag("openai.request.model") or ""
        tags = [
            "version:%s" % (config.version or ""),
            "env:%s" % (config.env or ""),
            "service:%s" % (span.service or ""),
            "openai.request.model:%s" % model_name,
            "model:%s" % model_name,
            "openai.request.endpoint:%s" % (span.get_tag("openai.request.endpoint") or ""),
            "openai.request.method:%s" % (span.get_tag("openai.request.method") or ""),
            "openai.organization.id:%s" % (span.get_tag("openai.organization.id") or ""),
            "openai.organization.name:%s" % (span.get_tag("openai.organization.name") or ""),
            "openai.user.api_key:%s" % (span.get_tag("openai.user.api_key") or ""),
            "error:%d" % span.error,
        ]
        err_type = span.get_tag("error.type")
        if err_type:
            tags.append("error_type:%s" % err_type)
        return tags

    def record_usage(self, span: Span, usage: Dict[str, Any]) -> None:
        if not usage or not self.metrics_enabled:
            return
        tags = ["openai.estimated:false"]
        for token_type in ("prompt", "completion", "total"):
            num_tokens = getattr(usage, token_type + "_tokens", None)
            if not num_tokens:
                continue
            span.set_metric("openai.response.usage.%s_tokens" % token_type, num_tokens)
            self.metric(span, "dist", "tokens.%s" % token_type, num_tokens, tags=tags)

    def llmobs_set_tags(
        self,
        operation: str,  # oneof "completion", "chat", "embedding"
        resp: Any,
        span: Span,
        kwargs: Dict[str, Any],
        streamed_completions: Optional[Any] = None,
        err: Optional[Any] = None,
    ) -> None:
        """Sets meta tags and metrics for span events to be sent to LLMObs."""
        if not self.llmobs_enabled:
            return
        span_kind = "embedding" if operation == "embedding" else "llm"
        span.set_tag_str(SPAN_KIND, span_kind)
        model_name = span.get_tag("openai.response.model") or span.get_tag("openai.request.model")
        span.set_tag_str(MODEL_NAME, model_name or "")
        span.set_tag_str(MODEL_PROVIDER, "openai")
        if operation == "completion":
            self._llmobs_set_meta_tags_from_completion(resp, err, kwargs, streamed_completions, span)
        elif operation == "chat":
            self._llmobs_set_meta_tags_from_chat(resp, err, kwargs, streamed_completions, span)
        elif operation == "embedding":
            self._llmobs_set_meta_tags_from_embedding(resp, err, kwargs, span)
        metrics = self._set_llmobs_metrics_tags(span, resp, streamed_completions is not None)
        span.set_tag_str(METRICS, safe_json(metrics))

    @staticmethod
    def _llmobs_set_meta_tags_from_completion(
        resp: Any, err: Any, kwargs: Dict[str, Any], streamed_completions: Optional[Any], span: Span
    ) -> None:
        """Extract prompt/response tags from a completion and set them as temporary "_ml_obs.meta.*" tags."""
        prompt = kwargs.get("prompt", "")
        if isinstance(prompt, str):
            prompt = [prompt]
        span.set_tag_str(INPUT_MESSAGES, safe_json([{"content": str(p)} for p in prompt]))

        parameters = {k: v for k, v in kwargs.items() if k not in ("model", "prompt")}
        span.set_tag_str(METADATA, safe_json(parameters))

        if err is not None:
            span.set_tag_str(OUTPUT_MESSAGES, safe_json([{"content": ""}]))
            return
        if streamed_completions:
            messages = [{"content": _get_attr(choice, "text", "")} for choice in streamed_completions]
        else:
            messages = [{"content": _get_attr(choice, "text", "")} for choice in resp.choices]
        span.set_tag_str(OUTPUT_MESSAGES, safe_json(messages))

    @staticmethod
    def _llmobs_set_meta_tags_from_chat(
        resp: Any, err: Any, kwargs: Dict[str, Any], streamed_messages: Optional[Any], span: Span
    ) -> None:
        """Extract prompt/response tags from a chat completion and set them as temporary "_ml_obs.meta.*" tags."""
        input_messages = []
        for m in kwargs.get("messages", []):
            input_messages.append({"content": str(_get_attr(m, "content", "")), "role": str(_get_attr(m, "role", ""))})
        span.set_tag_str(INPUT_MESSAGES, safe_json(input_messages))

        parameters = {k: v for k, v in kwargs.items() if k not in ("model", "messages", "tools", "functions")}
        span.set_tag_str(METADATA, safe_json(parameters))

        if err is not None:
            span.set_tag_str(OUTPUT_MESSAGES, safe_json([{"content": ""}]))
            return
        if streamed_messages:
            messages = []
            for message in streamed_messages:
                if "formatted_content" in message:
                    messages.append({"content": message["formatted_content"], "role": message["role"]})
                    continue
                messages.append({"content": message["content"], "role": message["role"]})
            span.set_tag_str(OUTPUT_MESSAGES, safe_json(messages))
            return
        output_messages = []
        for idx, choice in enumerate(resp.choices):
            tool_calls_info = []
            content = getattr(choice.message, "content", "")
            if getattr(choice.message, "function_call", None):
                function_call_info = {
                    "name": getattr(choice.message.function_call, "name", ""),
                    "arguments": json.loads(getattr(choice.message.function_call, "arguments", "")),
                }
                if content is None:
                    content = ""
                output_messages.append(
                    {"content": content, "role": choice.message.role, "tool_calls": [function_call_info]}
                )
            elif getattr(choice.message, "tool_calls", None):
                for tool_call in choice.message.tool_calls:
                    tool_call_info = {
                        "name": getattr(tool_call.function, "name", ""),
                        "arguments": json.loads(getattr(tool_call.function, "arguments", "")),
                        "tool_id": getattr(tool_call, "id", ""),
                        "type": getattr(tool_call, "type", ""),
                    }
                    tool_calls_info.append(tool_call_info)
                if content is None:
                    content = ""
                output_messages.append({"content": content, "role": choice.message.role, "tool_calls": tool_calls_info})
            else:
                output_messages.append({"content": content, "role": choice.message.role})
        span.set_tag_str(OUTPUT_MESSAGES, safe_json(output_messages))

    @staticmethod
    def _llmobs_set_meta_tags_from_embedding(resp: Any, err: Any, kwargs: Dict[str, Any], span: Span) -> None:
        """Extract prompt tags from an embedding and set them as temporary "_ml_obs.meta.*" tags."""
        encoding_format = kwargs.get("encoding_format") or "float"
        metadata = {"encoding_format": encoding_format}
        if kwargs.get("dimensions"):
            metadata["dimensions"] = kwargs.get("dimensions")
        span.set_tag_str(METADATA, safe_json(metadata))

        embedding_inputs = kwargs.get("input", "")
        if isinstance(embedding_inputs, str) or isinstance(embedding_inputs[0], int):
            embedding_inputs = [embedding_inputs]
        input_documents = []
        for doc in embedding_inputs:
            input_documents.append(Document(text=str(doc)))
        span.set_tag_str(INPUT_DOCUMENTS, safe_json(input_documents))

        if err is not None:
            return
        if encoding_format == "float":
            embedding_dim = len(resp.data[0].embedding)
            span.set_tag_str(
                OUTPUT_VALUE, "[{} embedding(s) returned with size {}]".format(len(resp.data), embedding_dim)
            )
            return
        span.set_tag_str(OUTPUT_VALUE, "[{} embedding(s) returned]".format(len(resp.data)))

    @staticmethod
    def _set_llmobs_metrics_tags(span: Span, resp: Any, streamed: bool = False) -> Dict[str, Any]:
        """Extract metrics from a chat/completion and set them as a temporary "_ml_obs.metrics" tag."""
        metrics = {}
        if streamed:
            prompt_tokens = span.get_metric("openai.response.usage.prompt_tokens") or 0
            completion_tokens = span.get_metric("openai.response.usage.completion_tokens") or 0
            metrics.update(
                {
                    INPUT_TOKENS_METRIC_KEY: prompt_tokens,
                    OUTPUT_TOKENS_METRIC_KEY: completion_tokens,
                    TOTAL_TOKENS_METRIC_KEY: prompt_tokens + completion_tokens,
                }
            )
        elif resp:
            prompt_tokens = getattr(resp.usage, "prompt_tokens", 0)
            completion_tokens = getattr(resp.usage, "completion_tokens", 0)
            metrics.update(
                {
                    INPUT_TOKENS_METRIC_KEY: prompt_tokens,
                    OUTPUT_TOKENS_METRIC_KEY: completion_tokens,
                    TOTAL_TOKENS_METRIC_KEY: prompt_tokens + completion_tokens,
                }
            )
        return metrics
