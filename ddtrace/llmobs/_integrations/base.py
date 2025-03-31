import abc
import os
from typing import Any  # noqa:F401
from typing import Dict  # noqa:F401
from typing import List  # noqa:F401
from typing import Optional  # noqa:F401

from ddtrace import config
from ddtrace._trace.sampler import RateSampler
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.ext import SpanTypes
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import asbool
from ddtrace.llmobs._constants import INTEGRATION
from ddtrace.llmobs._llmobs import LLMObs
from ddtrace.settings import IntegrationConfig
from ddtrace.trace import Pin
from ddtrace.trace import Span


log = get_logger(__name__)


class BaseLLMIntegration:
    _integration_name = "baseLLM"

    def __init__(self, integration_config: IntegrationConfig) -> None:
        self.integration_config = integration_config
        self._span_pc_sampler = RateSampler(
            sample_rate=getattr(integration_config, "span_prompt_completion_sample_rate", 1.0)
        )
        self._llmobs_pc_sampler = RateSampler(sample_rate=config._llmobs_sample_rate)

    @property
    def span_linking_enabled(self) -> bool:
        return asbool(os.getenv("_DD_LLMOBS_AUTO_SPAN_LINKING_ENABLED", "false")) or asbool(
            os.getenv("_DD_TRACE_LANGGRAPH_ENABLED", "false")
        )

    @property
    def llmobs_enabled(self) -> bool:
        """Return whether submitting llmobs payloads is enabled."""
        return LLMObs.enabled

    def is_pc_sampled_span(self, span: Span) -> bool:
        if span.context.sampling_priority is not None and span.context.sampling_priority <= 0:
            return False
        return self._span_pc_sampler.sample(span)

    def is_pc_sampled_llmobs(self, span: Span) -> bool:
        # Sampling of llmobs payloads is independent of spans, but we're using a RateSampler for consistency.
        if not self.llmobs_enabled:
            return False
        return self._llmobs_pc_sampler.sample(span)

    @abc.abstractmethod
    def _set_base_span_tags(self, span: Span, **kwargs) -> None:
        """Set default LLM span attributes when possible."""
        pass

    def trace(self, pin: Pin, operation_id: str, submit_to_llmobs: bool = False, **kwargs: Dict[str, Any]) -> Span:
        """
        Start a LLM request span.
        Reuse the service of the application since we'll tag downstream request spans with the LLM name.
        Eventually those should also be internal service spans once peer.service is implemented.
        """
        span = pin.tracer.trace(
            "%s.request" % self._integration_name,
            resource=operation_id,
            service=int_service(pin, self.integration_config),
            span_type=SpanTypes.LLM if (submit_to_llmobs and self.llmobs_enabled) else None,
        )
        # Enable trace metrics for these spans so users can see per-service openai usage in APM.
        span.set_tag(_SPAN_MEASURED_KEY)
        self._set_base_span_tags(span, **kwargs)
        if self.llmobs_enabled:
            span._set_ctx_item(INTEGRATION, self._integration_name)
        return span

    def trunc(self, text: str) -> str:
        """Truncate the given text.

        Use to avoid attaching too much data to spans.
        """
        if not text:
            return text
        text = text.replace("\n", "\\n").replace("\t", "\\t")
        if len(text) > self.integration_config.span_char_limit:
            text = text[: self.integration_config.span_char_limit] + "..."
        return text

    def llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        """Extract input/output information from the request and response to be submitted to LLMObs."""
        if not self.llmobs_enabled:
            return
        try:
            self._llmobs_set_tags(span, args, kwargs, response, operation)
        except Exception:
            log.error("Error extracting LLMObs fields for span %s, likely due to malformed data", span, exc_info=True)

    @abc.abstractmethod
    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        raise NotImplementedError()
