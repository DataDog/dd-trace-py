import time
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from ddtrace import Span
from ddtrace import config
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.llmobs.llm_integrations.base import BaseLLMIntegration
from ddtrace.internal.utils.version import parse_version


class OpenAIIntegration(BaseLLMIntegration):
    _integration_name = "openai"

    def __init__(self, config, openai, stats_url):
        # FIXME: this currently does not consider if the tracer is configured to
        # use a different hostname. eg. tracer.configure(host="new-hostname")
        # Ideally the metrics client should live on the tracer or some other core
        # object that is strongly linked with configuration.
        super().__init__(config, stats_url)
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

    def _set_base_span_tags(self, span: Span, **kwargs) -> None:
        span.set_tag_str(COMPONENT, self._config.integration_name)
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
        tags = [
            "version:%s" % (config.version or ""),
            "env:%s" % (config.env or ""),
            "service:%s" % (span.service or ""),
            "openai.request.model:%s" % (span.get_tag("openai.request.model") or ""),
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
        tags = self._metrics_tags(span)
        tags.append("openai.estimated:false")
        for token_type in ("prompt", "completion", "total"):
            num_tokens = getattr(usage, token_type + "_tokens", None)
            if not num_tokens:
                continue
            span.set_metric("openai.response.usage.%s_tokens" % token_type, num_tokens)
            self.metric(span, "dist", "tokens.%s" % token_type, num_tokens, tags=tags)

    def generate_completion_llm_records(self, resp: Any, span: Span, args: List[Any], kwargs: Dict[str, Any]) -> None:
        """Generate payloads for the LLM Obs API from a completion."""
        if not self.llmobs_enabled:
            return
        choices = resp.choices
        n = kwargs.get("n", 1)
        prompt = kwargs.get("prompt", "")
        now = time.time()
        if isinstance(prompt, str):
            prompt = [prompt]
        # Note: LLMObs ingest endpoint only accepts a 1:1 prompt-response mapping per record,
        #  so we need to deduplicate and send unique prompt-response records if n > 1.
        for i in range(n):
            unique_choices = choices[i::n]
            attrs_dict = {
                "type": "completion",
                "id": resp.id,
                "timestamp": resp.created * 1000,
                "model": span.get_tag("openai.request.model") or resp.model,
                "model_provider": "openai",
                "input": {
                    "prompts": prompt,
                    "temperature": kwargs.get("temperature"),
                    "max_tokens": kwargs.get("max_tokens"),
                },
                "output": {
                    "completions": [{"content": choice.text} for choice in unique_choices],
                    "durations": [now - span.start for _ in unique_choices],
                },
            }
            self.llm_record(span, attrs_dict)

    def generate_chat_llm_records(self, resp: Any, span: Span, args: List[Any], kwargs: Dict[str, Any]) -> None:
        """Generate payloads for the LLM Obs API from a chat completion."""
        if not self.llmobs_enabled:
            return
        choices = resp.choices
        now = time.time()
        # Note: LLMObs ingest endpoint only accepts a 1:1 prompt-response mapping per record,
        #  so we need to send unique prompt-response records if there are multiple responses (n > 1).
        for choice in choices:
            messages = kwargs.get("messages", [])
            content = getattr(choice.message, "content", None)
            if getattr(choice.message, "function_call", None):
                content = choice.message.function_call.arguments
            elif getattr(choice.message, "tool_calls", None):
                content = choice.message.tool_calls.function.arguments
            attrs_dict = {
                "type": "chat",
                "id": resp.id,
                "timestamp": resp.created * 1000,
                "model": span.get_tag("openai.request.model") or resp.model,
                "model_provider": "openai",
                "input": {
                    "messages": [{"content": str(m.get("content", "")), "role": m.get("role", "")} for m in messages],
                    "temperature": kwargs.get("temperature"),
                    "max_tokens": kwargs.get("max_tokens"),
                },
                "output": {
                    "completions": [
                        {
                            "content": str(content),
                            "role": choice.message.role,
                        }
                    ],
                    "durations": [now - span.start],
                },
            }
            self.llm_record(span, attrs_dict)
