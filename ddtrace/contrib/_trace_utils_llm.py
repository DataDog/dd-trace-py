import abc
import os
import time
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import TYPE_CHECKING

from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.contrib.trace_utils import int_service
from ddtrace.internal.dogstatsd import get_dogstatsd_client
from ddtrace.internal.hostname import get_hostname
from ddtrace.internal.log_writer import V2LogWriter
from ddtrace.sampler import RateSampler


if TYPE_CHECKING:
    from ddtrace import Pin
    from ddtrace import Span


class BaseLLMIntegration:
    _integration_name = "baseLLM"

    def __init__(self, config, stats_url, site, api_key):
        # FIXME: this currently does not consider if the tracer is configured to
        # use a different hostname. eg. tracer.configure(host="new-hostname")
        # Ideally the metrics client should live on the tracer or some other core
        # object that is strongly linked with configuration.
        self._statsd = get_dogstatsd_client(stats_url, namespace=self._integration_name)
        self._config = config
        self._log_writer = V2LogWriter(
            site=site,
            api_key=api_key,
            interval=float(os.getenv("_DD_%s_LOG_WRITER_INTERVAL" % self._integration_name.upper(), "1.0")),
            timeout=float(os.getenv("_DD_%s_LOG_WRITER_TIMEOUT" % self._integration_name.upper(), "2.0")),
        )
        self._span_pc_sampler = RateSampler(sample_rate=config.span_prompt_completion_sample_rate)
        self._log_pc_sampler = RateSampler(sample_rate=config.log_prompt_completion_sample_rate)

    def is_pc_sampled_span(self, span):
        # type: (Span) -> bool
        if not span.sampled:
            return False
        return self._span_pc_sampler.sample(span)

    def is_pc_sampled_log(self, span):
        # type: (Span) -> bool
        if not self._config.logs_enabled or not span.sampled:
            return False
        return self._log_pc_sampler.sample(span)

    def start_log_writer(self):
        # type: (...) -> None
        self._log_writer.start()

    @abc.abstractmethod
    def _set_base_span_tags(self, span, **kwargs):
        # type: (Span, Dict[str, Any]) -> None
        """Set default LLM span attributes when possible."""
        pass

    def trace(self, pin, operation_id, **kwargs):
        # type: (Pin, str, Dict[str, Any]) -> Span
        """
        Start a LLM request span.
        Reuse the service of the application since we'll tag downstream request spans with the LLM name.
        Eventually those should also be internal service spans once peer.service is implemented.
        """
        span = pin.tracer.trace(
            "%s.request" % self._integration_name, resource=operation_id, service=int_service(pin, self._config)
        )
        # Enable trace metrics for these spans so users can see per-service openai usage in APM.
        span.set_tag(SPAN_MEASURED_KEY)
        self._set_base_span_tags(span, **kwargs)
        return span

    @classmethod
    @abc.abstractmethod
    def _logs_tags(cls, span):
        # type: (Span) -> str
        """Generate ddtags from the corresponding span."""
        pass

    def log(self, span, level, msg, attrs):
        # type: (Span, str, str, Dict[str, Any]) -> None
        if not self._config.logs_enabled:
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
            log["dd.trace_id"] = str(span.trace_id)
            log["dd.span_id"] = str(span.span_id)
        log.update(attrs)
        self._log_writer.enqueue(log)

    @classmethod
    @abc.abstractmethod
    def _metrics_tags(cls, span):
        # type: (Span) -> list
        """Generate a list of metrics tags from a given span."""
        return []

    def metric(self, span, kind, name, val, tags=None):
        # type: (Span, str, str, Any, Optional[List[str]]) -> None
        """Set a metric using the context from the given span."""
        if not self._config.metrics_enabled:
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

    def trunc(self, text):
        # type: (str) -> str
        """Truncate the given text.

        Use to avoid attaching too much data to spans.
        """
        if not text:
            return text
        text = text.replace("\n", "\\n").replace("\t", "\\t")
        if len(text) > self._config.span_char_limit:
            text = text[: self._config.span_char_limit] + "..."
        return text
