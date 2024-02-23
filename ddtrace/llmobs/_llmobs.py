import json
import os
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

import ddtrace
from ddtrace import Span
from ddtrace import config
from ddtrace._trace.processor import TraceProcessor
from ddtrace.ext import SpanTypes
from ddtrace.internal import atexit
from ddtrace.internal.logger import get_logger
from ddtrace.internal.service import Service
from ddtrace.llmobs._writer import LLMObsWriter


log = get_logger(__name__)


class LLMObs(Service):
    _instance = None
    enabled = False

    def __init__(self, tracer=None):
        super(LLMObs, self).__init__()
        self.tracer = tracer or ddtrace.tracer
        self._llmobs_writer = LLMObsWriter(
            site=config._dd_site,
            api_key=config._dd_api_key,
            interval=float(os.getenv("_DD_LLMOBS_WRITER_INTERVAL", 1.0)),
            timeout=float(os.getenv("_DD_LLMOBS_WRITER_TIMEOUT", 2.0)),
        )
        self._llmobs_writer.start()

    def _start_service(self) -> None:
        tracer_filters = self.tracer._filters
        if not any(isinstance(tracer_filter, LLMObsTraceProcessor) for tracer_filter in tracer_filters):
            tracer_filters += [LLMObsTraceProcessor(self._llmobs_writer)]
        self.tracer.configure(settings={"FILTERS": tracer_filters})

    def _stop_service(self) -> None:
        try:
            self.tracer.shutdown()
        except Exception:
            log.warning("Failed to shutdown tracer", exc_info=True)

    @classmethod
    def enable(cls, tracer=None):
        if cls._instance is not None:
            log.debug("%s already enabled", cls.__name__)
            return

        if not config._dd_api_key:
            cls.enabled = False
            raise ValueError("DD_API_KEY is required for sending LLMObs data")

        cls._instance = cls(tracer=tracer)
        cls.enabled = True
        cls._instance.start()
        atexit.register(cls.disable)
        log.debug("%s enabled", cls.__name__)

    @classmethod
    def disable(cls) -> None:
        if cls._instance is None:
            log.debug("%s not enabled", cls.__name__)
            return
        log.debug("Disabling %s", cls.__name__)
        atexit.unregister(cls.disable)

        cls._instance.stop()
        cls._instance = None
        cls.enabled = False
        log.debug("%s disabled", cls.__name__)


class LLMObsTraceProcessor(TraceProcessor):
    """
    Processor that extracts LLM-type spans in a trace to submit as separate LLMObs span events to LLM Observability.
    """

    def __init__(self, llmobs_writer):
        self._writer = llmobs_writer

    def process_trace(self, trace: List[Span]) -> Optional[List[Span]]:
        if not trace:
            return None
        for span in trace:
            if span.span_type == SpanTypes.LLM:
                self.submit_llmobs_span(span)
        return trace

    @staticmethod
    def _llmobs_tags(span: Span, meta: Dict[str, Any]) -> List[str]:
        tags = [
            "version:%s" % (config.version or ""),
            "env:%s" % (config.env or ""),
            "service:%s" % (span.service or ""),
            "source:integration",
            "model_name:{}".format(meta.get("model_name", "")),
            "model_provider:{}".format(meta.get("model_provider", "custom")),
            "error:%d" % span.error,
        ]
        err_type = span.get_tag("error.type")
        if err_type:
            tags.append("error_type:%s" % err_type)
        return tags

    def submit_llmobs_span(self, span: Span) -> None:
        """Generate and submit an LLMObs span event to be sent to LLMObs."""
        meta = json.loads(span._meta.pop("ml_obs.meta", "{}"))
        if span.error:
            meta["error.message"] = span.get_tag("error.message")
        metrics = json.loads(span._meta.pop("ml_obs.metrics", "{}"))
        span_event = self._llmobs_span_event(span, meta, metrics)
        log.debug("Submitting span to LLMObs: %s", span)
        self._writer.enqueue(span_event)

    def _llmobs_span_event(self, span: Span, meta: Dict[str, Any], metrics: Dict[str, Any]) -> Dict[str, Any]:
        """Span event object structure."""
        return {
            "trace_id": "{:x}".format(span.trace_id),
            "span_id": str(span.span_id),
            "parent_id": str(self._get_llmobs_parent_id(span) or ""),
            "session_id": "{:x}".format(span.trace_id),
            "tags": self._llmobs_tags(span, meta),
            "name": span.name,
            "error": span.error,
            "start_ns": span.start_ns,
            "duration": span.duration_ns,
            "meta": meta,
            "metrics": metrics,
        }

    @staticmethod
    def _get_llmobs_parent_id(span: Span) -> Optional[int]:
        """Return the span ID of the nearest LLMObs-type span in the span's ancestor tree."""
        if span.span_type != SpanTypes.LLM:
            return None
        parent = span._parent
        while parent:
            if parent.span_type == SpanTypes.LLM:
                return parent.span_id
            parent = parent._parent
        return None
