from typing import Any
from typing import Dict
from typing import List
from typing import Optional

import json

from ddtrace import config
from ddtrace._trace.span import Span
from ddtrace.constants import ERROR_TYPE

from ddtrace.internal.utils import get_argument_value

from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import INPUT_PARAMETERS
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import SPAN_KIND

from .base import BaseLLMIntegration


API_KEY = "langchain.request.api_key"
MODEL = "langchain.request.model"
PROVIDER = "langchain.request.provider"
TOTAL_COST = "langchain.tokens.total_cost"
TYPE = "langchain.request.type"


class LangChainIntegration(BaseLLMIntegration):
    _integration_name = "langchain"

    def llmobs_set_tags(
        self,
        operation: str,  # oneof "llm","chat_model","chain"
        span: Span,
        response: Any,
        kwargs: Dict[str, Any],
        args: List[Any],
        err: bool = False,
    ) -> None:
        """Sets meta tags and metrics for span events to be sent to LLMObs."""
        if not self.llmobs_enabled:
            return
        model_provider = span.get_tag(PROVIDER)
        span.set_tag_str(SPAN_KIND, "llm")
        span.set_tag_str(MODEL_NAME, span.get_tag(MODEL) or "")
        span.set_tag_str(MODEL_PROVIDER, model_provider or "")

        # input parameters
        if model_provider:
            input_parameters = {}
            temperature = span.get_tag(f"langchain.request.{model_provider}.parameters.temperature")
            max_tokens = span.get_tag(f"langchain.request.{model_provider}.parameters.max_tokens")
            if temperature:
                input_parameters["temperature"] = temperature
            if max_tokens:
                input_parameters["max_tokens"] = max_tokens
            if input_parameters:
                span.set_tag_str(INPUT_PARAMETERS, json.dumps(input_parameters))

        if operation == "llm":
            self._llmobs_set_meta_tags_from_llm(span, args, kwargs, response, err)
        elif operation == "chat_model":
            self._llmobs_set_meta_tags_from_chat_model(span, args, kwargs, response, err)
        elif operation == "chain":
            # TODO to be added as a follow-up PR
            pass
        self._llmobs_set_metrics_tags(span)

    def _llmobs_set_meta_tags_from_llm(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        completions: List[str],
        err: bool = False,
    ) -> None:
        prompts = get_argument_value(args, kwargs, 0, "prompts")

        # input messages
        if isinstance(prompts, str):
            prompts = [prompts]
        span.set_tag_str(INPUT_MESSAGES, json.dumps([{"content": str(prompt)} for prompt in prompts]))

        # output messages
        message_content = [{"content": ""}]
        if err is None:
            message_content = [{"content": self.trunc(str(completion[0].text))} for completion in completions]
        span.set_tag_str(OUTPUT_MESSAGES, json.dumps(message_content))

    def _llmobs_set_meta_tags_from_chat_model(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        chat_completions: List[str],
        err: bool = False,
    ) -> None:
        chat_messages = get_argument_value(args, kwargs, 0, "messages")

        # input messages
        span.set_tag_str(
            INPUT_MESSAGES,
            json.dumps(
                [
                    (
                        {
                            "content": self.trunc(message.content),
                            "role": str(message.type),
                        }
                    for message in message_set)
                for message_set in chat_messages]
            ),
        )

        # output messages
        message_content = [{"content": ""}]
        if err is None:
            message_content = [
                ({"content": self.trunc(chat_completion.text)} for chat_completion in message_set)
                for message_set in chat_completions.generations
            ]
        span.set_tag_str(OUTPUT_MESSAGES, json.dumps(message_content))

    def _llmobs_set_metrics_tags(
        self,
        span: Span,
    ) -> None:
        metrics = {}
        prompt_tokens = span.get_tag("langchain.tokens.prompt_tokens")
        completion_tokens = span.get_tag("langchain.tokens.completion_tokens")
        total_tokens = span.get_tag("langchain.tokens.total_tokens")  # in the case it is not the sum?

        if prompt_tokens:
            metrics["prompt_tokens"] = int(prompt_tokens)
        if completion_tokens:
            metrics["completion_tokens"] = int(completion_tokens)
        if total_tokens:
            metrics["total_tokens"] = int(total_tokens)

        span.set_tag_str(METRICS, json.dumps(metrics))

    def _set_base_span_tags(  # type: ignore[override]
        self,
        span: Span,
        interface_type: str = "",
        provider: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        """Set base level tags that should be present on all LangChain spans (if they are not None)."""
        span.set_tag_str(TYPE, interface_type)
        if provider is not None:
            span.set_tag_str(PROVIDER, provider)
        if model is not None:
            span.set_tag_str(MODEL, model)
        if api_key is not None:
            if len(api_key) >= 4:
                span.set_tag_str(API_KEY, "...%s" % str(api_key[-4:]))
            else:
                span.set_tag_str(API_KEY, api_key)

    @classmethod
    def _logs_tags(cls, span: Span) -> str:
        api_key = span.get_tag(API_KEY) or ""
        tags = "env:%s,version:%s,%s:%s,%s:%s,%s:%s,%s:%s" % (  # noqa: E501
            (config.env or ""),
            (config.version or ""),
            PROVIDER,
            (span.get_tag(PROVIDER) or ""),
            MODEL,
            (span.get_tag(MODEL) or ""),
            TYPE,
            (span.get_tag(TYPE) or ""),
            API_KEY,
            api_key,
        )
        return tags

    @classmethod
    def _metrics_tags(cls, span: Span) -> List[str]:
        provider = span.get_tag(PROVIDER) or ""
        api_key = span.get_tag(API_KEY) or ""
        tags = [
            "version:%s" % (config.version or ""),
            "env:%s" % (config.env or ""),
            "service:%s" % (span.service or ""),
            "%s:%s" % (PROVIDER, provider),
            "%s:%s" % (MODEL, span.get_tag(MODEL) or ""),
            "%s:%s" % (TYPE, span.get_tag(TYPE) or ""),
            "%s:%s" % (API_KEY, api_key),
            "error:%d" % span.error,
        ]
        err_type = span.get_tag(ERROR_TYPE)
        if err_type:
            tags.append("%s:%s" % (ERROR_TYPE, err_type))
        return tags

    def record_usage(self, span: Span, usage: Dict[str, Any]) -> None:
        if not usage or self.metrics_enabled is False:
            return
        for token_type in ("prompt", "completion", "total"):
            num_tokens = usage.get("token_usage", {}).get(token_type + "_tokens")
            if not num_tokens:
                continue
            self.metric(span, "dist", "tokens.%s" % token_type, num_tokens)
        total_cost = span.get_metric(TOTAL_COST)
        if total_cost:
            self.metric(span, "incr", "tokens.total_cost", total_cost)
