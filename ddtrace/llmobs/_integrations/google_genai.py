from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._utils import _get_attr
from ddtrace._trace.span import Span
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.internal.utils import get_argument_value


class GoogleGenAIIntegration(BaseLLMIntegration):
    _integration_name = "google_genai"

    def _set_base_span_tags(
        self, span: Span, provider: Optional[str] = None, model: Optional[str] = None, **kwargs: Dict[str, Any]
    ) -> None:
        if provider is not None:
            span.set_tag_str("google_genai.request.provider", provider)
        if model is not None:
            span.set_tag_str("google_genai.request.model", model)

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
         config = get_argument_value(args, kwargs, -1, "config", optional=True)
         metadata =  llmobs_get_metadata_google_genai(config)
         input_messages = None
         output_messages = None
         metrics = None

         span._set_ctx_items(
            {
                SPAN_KIND: "llm",
                MODEL_NAME: span.get_tag("google_genai.request.model") or "",
                MODEL_PROVIDER: span.get_tag("google_genai.request.provider") or "",
                METADATA: metadata,
                # INPUT_MESSAGES: input_messages,
                # OUTPUT_MESSAGES: output_messages,
                # METRICS: get_llmobs_metrics_tags("vertexai", span),
            }
        )

def llmobs_get_metadata_google_genai(config):
    # if not config:
    #     return {}
    # metadata = {}
    # parameters = ("temperature", "max_output_tokens", "candidate_count", "top_p", "top_k")
    # for param in parameters:
    #     config_value = _get_attr(config, param, None)
    #     if config_value:
    #         metadata[param] = config_value
    # return metadata
    # alternatively, can convert config direcrtly to a dict and feed it in as metadata
    return config.model_dump() if config else {}
