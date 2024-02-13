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
    """Processor that marks all spans in a trace that have an LLM-type span.

    1. If a span in a trace is an LLM-type span, we'll write the whole trace to LLMObs.
    2. any leaf spans in this trace will be marked as task-type spans.
    3. any non-leaf spans in this trace will be marked as chain/workflow-type spans.

    # TODO: handle distributed cases (need to propagate LLMObs status in the response)
    """

    def __init__(self, llmobs_writer):
        self._writer = llmobs_writer

    def process_trace(self, trace: List[Span]) -> Optional[List[Span]]:
        if not trace:
            return None
        trace_contains_llm = False
        for span in trace:
            if span.span_type == SpanTypes.LLM:
                trace_contains_llm = True
                self.submit_llmobs_span(span)
        if not trace_contains_llm:
            return trace

        for span in trace:
            if self._has_children(trace, span):
                span.set_tag_str("ml_obs.kind", "chain")
            else:
                span.set_tag_str("ml_obs.kind", "task")

        # TODO: Need to infer span kind and submit to LLMObsWriter

        return trace

    @staticmethod
    def _has_children(trace: List[Span], span: Span) -> bool:
        for s in trace:
            if s.parent_id == span.span_id:
                return True
        return False

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

    def submit_llmobs_span(self, span):
        """Generate and submit an LLMObs span event to be sent to LLMObs."""
        meta = json.loads(span._meta.pop("ml_obs.meta", {}))
        metrics = json.loads(span._meta.pop("ml_obs.metrics", {}))
        span_event = self._llmobs_span_event(span, meta, metrics)
        log.debug("Submitting span to LLMObs: %s", span)
        self._writer.enqueue(span_event)

    def _llmobs_span_event(self, span, meta, metrics):
        """Span event object structure."""
        return {
            "trace_id": "{:x}".format(span.trace_id),
            "span_id": str(span.span_id),
            "parent_id": str(span.parent_id or ""),
            "session_id": "{:x}".format(span.trace_id),
            "apm_context": {"span_id": str(span.span_id), "trace_id": "{:x}".format(span.trace_id)},
            "tags": self._llmobs_tags(span, meta),
            "name": span.name,
            "kind": meta.pop("kind"),
            "start_ns": span.start_ns,
            "duration": span.duration_ns,
            "status": "error" if span.error else "ok",
            "status_message": span.get_tag("error.message") or "",
            "meta": meta,
            "metrics": metrics,
        }
