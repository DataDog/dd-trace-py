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
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
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
        if operation_id.endswith("Completion"):
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

    def llmobs_set_tags(
        self,
        record_type: str,
        resp: Any,
        span: Span,
        kwargs: Dict[str, Any],
        streamed_completions: Optional[Any] = None,
        err: Optional[Any] = None,
    ) -> None:
        """Sets meta tags and metrics for span events to be sent to LLMObs."""
        if not self.llmobs_enabled:
            return
        span.set_tag_str(SPAN_KIND, "llm")
        model_name = span.get_tag("openai.response.model") or span.get_tag("openai.request.model")
        span.set_tag_str(MODEL_NAME, model_name or "")
        span.set_tag_str(MODEL_PROVIDER, "openai")
        if record_type == "completion":
            self._llmobs_set_meta_tags_from_completion(resp, err, kwargs, streamed_completions, span)
        elif record_type == "chat":
            self._llmobs_set_meta_tags_from_chat(resp, err, kwargs, streamed_completions, span)
        span.set_tag_str(
            METRICS, json.dumps(self._set_llmobs_metrics_tags(span, resp, streamed_completions is not None))
        )

    @staticmethod
    def _llmobs_set_meta_tags_from_completion(
        resp: Any, err: Any, kwargs: Dict[str, Any], streamed_completions: Optional[Any], span: Span
    ) -> None:
        """Extract prompt/response tags from a completion and set them as temporary "_ml_obs.meta.*" tags."""
        prompt = kwargs.get("prompt", "")
        if isinstance(prompt, str):
            prompt = [prompt]
        span.set_tag_str(INPUT_MESSAGES, json.dumps([{"content": str(p)} for p in prompt]))
        parameters = {"temperature": kwargs.get("temperature", 0)}
        if kwargs.get("max_tokens"):
            parameters["max_tokens"] = kwargs.get("max_tokens")
        span.set_tag_str(METADATA, json.dumps(parameters))
        if err is not None:
            span.set_tag_str(OUTPUT_MESSAGES, json.dumps([{"content": ""}]))
            return
        if streamed_completions:
            span.set_tag_str(
                OUTPUT_MESSAGES, json.dumps([{"content": choice["text"]} for choice in streamed_completions])
            )
            return
        span.set_tag_str(OUTPUT_MESSAGES, json.dumps([{"content": choice.text} for choice in resp.choices]))

    @staticmethod
    def _llmobs_set_meta_tags_from_chat(
        resp: Any, err: Any, kwargs: Dict[str, Any], streamed_messages: Optional[Any], span: Span
    ) -> None:
        """Extract prompt/response tags from a chat completion and set them as temporary "_ml_obs.meta.*" tags."""
        input_messages = []
        for m in kwargs.get("messages", []):
            if isinstance(m, dict):
                input_messages.append({"content": str(m.get("content", "")), "role": str(m.get("role", ""))})
                continue
            input_messages.append({"content": str(getattr(m, "content", "")), "role": str(getattr(m, "role", ""))})
        span.set_tag_str(INPUT_MESSAGES, json.dumps(input_messages))
        parameters = {"temperature": kwargs.get("temperature", 0)}
        if kwargs.get("max_tokens"):
            parameters["max_tokens"] = kwargs.get("max_tokens")
        span.set_tag_str(METADATA, json.dumps(parameters))
        if err is not None:
            span.set_tag_str(OUTPUT_MESSAGES, json.dumps([{"content": ""}]))
            return
        if streamed_messages:
            messages = []
            for message in streamed_messages:
                if "formatted_content" in message:
                    messages.append({"content": message["formatted_content"], "role": message["role"]})
                    continue
                messages.append({"content": message["content"], "role": message["role"]})
            span.set_tag_str(OUTPUT_MESSAGES, json.dumps(messages))
            return
        output_messages = []
        for idx, choice in enumerate(resp.choices):
            content = getattr(choice.message, "content", "")
            if getattr(choice.message, "function_call", None):
                content = "[function: {}]\n\n{}".format(
                    getattr(choice.message.function_call, "name", ""),
                    getattr(choice.message.function_call, "arguments", ""),
                )
            elif getattr(choice.message, "tool_calls", None):
                content = ""
                for tool_call in choice.message.tool_calls:
                    content += "\n[tool: {}]\n\n{}\n".format(
                        getattr(tool_call.function, "name", ""),
                        getattr(tool_call.function, "arguments", ""),
                    )
            output_messages.append({"content": str(content).strip(), "role": choice.message.role})
        span.set_tag_str(OUTPUT_MESSAGES, json.dumps(output_messages))

    @staticmethod
    def _set_llmobs_metrics_tags(span: Span, resp: Any, streamed: bool = False) -> Dict[str, Any]:
        """Extract metrics from a chat/completion and set them as a temporary "_ml_obs.metrics" tag."""
        metrics = {}
        if streamed:
            prompt_tokens = span.get_metric("openai.response.usage.prompt_tokens") or 0
            completion_tokens = span.get_metric("openai.response.usage.completion_tokens") or 0
            metrics.update(
                {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                }
            )
        elif resp:
            metrics.update(
                {
                    "prompt_tokens": resp.usage.prompt_tokens,
                    "completion_tokens": resp.usage.completion_tokens,
                    "total_tokens": resp.usage.prompt_tokens + resp.usage.completion_tokens,
                }
            )
        return metrics
