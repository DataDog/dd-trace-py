from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace import Span
from ddtrace import config
from ddtrace.constants import ERROR_TYPE
from ddtrace.internal.llmobs.integrations import BaseLLMIntegration


API_KEY = "langchain.request.api_key"
MODEL = "langchain.request.model"
PROVIDER = "langchain.request.provider"
TOTAL_COST = "langchain.tokens.total_cost"
TYPE = "langchain.request.type"


class LangChainIntegration(BaseLLMIntegration):
    _integration_name = "langchain"

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
