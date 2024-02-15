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
from ddtrace.llmobs._integrations.base import BaseLLMIntegration


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
        tags = ["openai.estimated:false"]
        for token_type in ("prompt", "completion", "total"):
            num_tokens = getattr(usage, token_type + "_tokens", None)
            if not num_tokens:
                continue
            span.set_metric("openai.response.usage.%s_tokens" % token_type, num_tokens)
            self.metric(span, "dist", "tokens.%s" % token_type, num_tokens, tags=tags)

    def llmobs_set_tags(self, record_type: str, resp: Any, err: Any, span: Span, kwargs: Dict[str, Any]) -> None:
        """Sets meta tags and metrics for span events to be sent to LLMObs."""
        if not self.llmobs_enabled:
            return
        metrics = self._set_llmobs_metrics(resp)
        meta = {
            "model_name": span.get_tag("openai.response.model") or span.get_tag("openai.request.model"),
            "model_provider": "openai",
            "span.kind": "llm",
        }
        if record_type == "completion":
            meta = self._llmobs_set_completion_meta(resp, err, kwargs, meta)
        elif record_type == "chat":
            meta = self._llmobs_set_chat_meta(resp, err, kwargs, meta)
        # Since span tags have to be strings, we have to json dump the data here and load on the trace processor.
        span.set_tag_str("ml_obs.meta", json.dumps(meta))
        span.set_tag_str("ml_obs.metrics", json.dumps(metrics))

    @staticmethod
    def _llmobs_set_completion_meta(
        resp: Any, err: Any, kwargs: Dict[str, Any], meta: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Extract prompt/response tags from a completion."""
        prompt = kwargs.get("prompt", "")
        if isinstance(prompt, str):
            prompt = [prompt]
        meta["input"] = {"messages": [{"content": p} for p in prompt]}
        meta["input"]["parameters"] = {"temperature": kwargs.get("temperature", 0)}
        if kwargs.get("max_tokens"):
            meta["input"]["parameters"]["max_tokens"] = kwargs.get("max_tokens")
        if err is not None:
            meta["output"] = {"messages": [{"content": ""}]}
        else:
            meta["output"] = {"messages": [{"content": choice.text} for choice in resp.choices]}
        return meta

    @staticmethod
    def _llmobs_set_chat_meta(resp: Any, err: Any, kwargs: Dict[str, Any], meta: Dict[str, Any]) -> Dict[str, Any]:
        """Extract prompt/response tags from a chat completion."""
        meta["input"] = {
            "messages": [
                {"content": str(m.get("content", "")), "role": m.get("role", "")} for m in kwargs.get("messages", [])
            ],
            "parameters": {"temperature": kwargs.get("temperature", 0)},
        }
        if kwargs.get("max_tokens"):
            meta["input"]["parameters"]["max_tokens"] = kwargs.get("max_tokens")
        if err is not None:
            meta["output"] = {"messages": [{"content": ""}]}
        else:
            messages = []
            for choice in resp.choices:
                content = getattr(choice.message, "content", None)
                if getattr(choice.message, "function_call", None):
                    content = choice.message.function_call.arguments
                elif getattr(choice.message, "tool_calls", None):
                    content = choice.message.tool_calls.function.arguments
                messages.append({"content": str(content), "role": choice.message.role})
            meta["output"] = {"messages": messages}
        return meta

    @staticmethod
    def _set_llmobs_metrics(resp: Any) -> Dict[str, Any]:
        metrics = {}
        if resp:
            metrics.update(
                {
                    "prompt_tokens": resp.usage.prompt_tokens,
                    "completion_tokens": resp.usage.completion_tokens,
                    "total_tokens": resp.usage.prompt_tokens + resp.usage.completion_tokens,
                }
            )
        return metrics
