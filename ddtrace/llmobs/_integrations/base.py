import abc
import os
import time
from typing import Any  # noqa:F401
from typing import Dict  # noqa:F401
from typing import List  # noqa:F401
from typing import Optional  # noqa:F401

from ddtrace import Pin
from ddtrace import config
from ddtrace._trace.span import Span
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.contrib.trace_utils import int_service
from ddtrace.internal.agent import get_stats_url
from ddtrace.internal.dogstatsd import get_dogstatsd_client
from ddtrace.internal.hostname import get_hostname
from ddtrace.internal.utils.formats import asbool
from ddtrace.llmobs._log_writer import V2LogWriter
from ddtrace.sampler import RateSampler
from ddtrace.settings import IntegrationConfig


class BaseLLMIntegration:
    _integration_name = "baseLLM"

    def __init__(self, integration_config: IntegrationConfig) -> None:
        # FIXME: this currently does not consider if the tracer is configured to
        # use a different hostname. eg. tracer.configure(host="new-hostname")
        # Ideally the metrics client should live on the tracer or some other core
        # object that is strongly linked with configuration.
        self._log_writer = None
        self._statsd = None
        self.integration_config = integration_config
        self._span_pc_sampler = RateSampler(sample_rate=integration_config.span_prompt_completion_sample_rate)

        if self.metrics_enabled:
            self._statsd = get_dogstatsd_client(get_stats_url(), namespace=self._integration_name)
        if self.logs_enabled:
            if not config._dd_api_key:
                raise ValueError(
                    f"DD_API_KEY is required for sending logs from the {self._integration_name} integration. "
                    f"To use the {self._integration_name} integration without logs, "
                    f"set `DD_{self._integration_name.upper()}_LOGS_ENABLED=false`."
                )
            self._log_writer = V2LogWriter(
                site=config._dd_site,
                api_key=config._dd_api_key,
                interval=float(os.getenv("_DD_%s_LOG_WRITER_INTERVAL" % self._integration_name.upper(), "1.0")),
                timeout=float(os.getenv("_DD_%s_LOG_WRITER_TIMEOUT" % self._integration_name.upper(), "2.0")),
            )
            self._log_pc_sampler = RateSampler(sample_rate=integration_config.log_prompt_completion_sample_rate)
            self.start_log_writer()
        if self.llmobs_enabled:
            self._llmobs_pc_sampler = RateSampler(sample_rate=config._llmobs_sample_rate)

    @property
    def metrics_enabled(self) -> bool:
        """Return whether submitting metrics is enabled for this integration, or global config if not set."""
        if hasattr(self.integration_config, "metrics_enabled"):
            return asbool(self.integration_config.metrics_enabled)
        return False

    @property
    def logs_enabled(self) -> bool:
        """Return whether submitting logs is enabled for this integration, or global config if not set."""
        if hasattr(self.integration_config, "logs_enabled"):
            return asbool(self.integration_config.logs_enabled)
        return False

    @property
    def llmobs_enabled(self) -> bool:
        """Return whether submitting llmobs payloads is enabled."""
        return config._llmobs_enabled

    def is_pc_sampled_span(self, span: Span) -> bool:
        if not span.sampled:
            return False
        return self._span_pc_sampler.sample(span)

    def is_pc_sampled_log(self, span: Span) -> bool:
        if not self.logs_enabled or not span.sampled:
            return False
        return self._log_pc_sampler.sample(span)

    def is_pc_sampled_llmobs(self, span: Span) -> bool:
        # Sampling of llmobs payloads is independent of spans, but we're using a RateSampler for consistency.
        if not self.llmobs_enabled:
            return False
        return self._llmobs_pc_sampler.sample(span)

    def start_log_writer(self) -> None:
        if not self.logs_enabled or self._log_writer is None:
            return
        self._log_writer.start()

    @abc.abstractmethod
    def _set_base_span_tags(self, span: Span, **kwargs) -> None:
        """Set default LLM span attributes when possible."""
        pass

    def trace(self, pin: Pin, operation_id: str, **kwargs: Dict[str, Any]) -> Span:
        """
        Start a LLM request span.
        Reuse the service of the application since we'll tag downstream request spans with the LLM name.
        Eventually those should also be internal service spans once peer.service is implemented.
        """
        span = pin.tracer.trace(
            "%s.request" % self._integration_name,
            resource=operation_id,
            service=int_service(pin, self.integration_config),
        )
        # Enable trace metrics for these spans so users can see per-service openai usage in APM.
        span.set_tag(SPAN_MEASURED_KEY)
        self._set_base_span_tags(span, **kwargs)
        return span

    @classmethod
    @abc.abstractmethod
    def _logs_tags(cls, span: Span) -> str:
        """Generate ddtags from the corresponding span."""
        pass

    def log(self, span: Span, level: str, msg: str, attrs: Dict[str, Any]) -> None:
        if not self.logs_enabled or self._log_writer is None:
            return
        tags = self._logs_tags(span)
        log = {
            "timestamp": time.time() * 1000,
            "message": msg,
            "hostname": get_hostname(),
            "ddsource": self._integration_name,
            "service": span.service or "",
            "status": level,
            "ddtags": tags,
        }
        if span is not None:
            # FIXME: this is a temporary workaround until we figure out why 128 bit trace IDs are stored as decimals.
            # log["dd.trace_id"] = str(span.trace_id)
            log["dd.trace_id"] = "{:x}".format(span.trace_id)
            log["dd.span_id"] = str(span.span_id)
        log.update(attrs)
        self._log_writer.enqueue(log)  # type: ignore[arg-type]

    @classmethod
    @abc.abstractmethod
    def _metrics_tags(cls, span: Span) -> List[str]:
        """Generate a list of metrics tags from a given span."""
        return []

    def metric(self, span: Span, kind: str, name: str, val: Any, tags: Optional[List[str]] = None) -> None:
        """Set a metric using the context from the given span."""
        if not self.metrics_enabled or self._statsd is None:
            return
        metric_tags = self._metrics_tags(span)
        if tags:
            metric_tags += tags
        if kind == "dist":
            self._statsd.distribution(name, val, tags=metric_tags)
        elif kind == "incr":
            self._statsd.increment(name, val, tags=metric_tags)
        elif kind == "gauge":
            self._statsd.gauge(name, val, tags=metric_tags)
        else:
            raise ValueError("Unexpected metric type %r" % kind)

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
