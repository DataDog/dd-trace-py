import json
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

import ddtrace
from ddtrace import Span
from ddtrace import config
from ddtrace._trace.processor import TraceProcessor
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE
from ddtrace.ext import SpanTypes
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import INPUT_PARAMETERS
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import ML_APP
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import SESSION_ID
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import TAGS


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

    def submit_llmobs_span(self, span: Span) -> None:
        """Generate and submit an LLMObs span event to be sent to LLMObs."""
        span_event = self._llmobs_span_event(span)
        self._writer.enqueue(span_event)

    def _llmobs_span_event(self, span: Span) -> Dict[str, Any]:
        """Span event object structure."""
        tags = self._llmobs_tags(span)
        meta: Dict[str, Any] = {"span.kind": span._meta.pop(SPAN_KIND), "input": {}, "output": {}}
        if span.get_tag(MODEL_NAME):
            meta["model_name"] = span._meta.pop(MODEL_NAME)
            meta["model_provider"] = span._meta.pop(MODEL_PROVIDER, "custom").lower()
        if span.get_tag(INPUT_PARAMETERS):
            meta["input"]["parameters"] = json.loads(span._meta.pop(INPUT_PARAMETERS))
        if span.get_tag(INPUT_MESSAGES):
            meta["input"]["messages"] = json.loads(span._meta.pop(INPUT_MESSAGES))
        if span.get_tag(INPUT_VALUE):
            meta["input"]["value"] = span._meta.pop(INPUT_VALUE)
        if span.get_tag(OUTPUT_MESSAGES):
            meta["output"]["messages"] = json.loads(span._meta.pop(OUTPUT_MESSAGES))
        if span.get_tag(OUTPUT_VALUE):
            meta["output"]["value"] = span._meta.pop(OUTPUT_VALUE)
        if span.error:
            meta[ERROR_MSG] = span.get_tag(ERROR_MSG)
            meta[ERROR_STACK] = span.get_tag(ERROR_STACK)
            meta[ERROR_TYPE] = span.get_tag(ERROR_TYPE)
        if not meta["input"]:
            meta.pop("input")
        if not meta["output"]:
            meta.pop("output")
        metrics = json.loads(span._meta.pop(METRICS, "{}"))
        session_id = self._get_session_id(span)
        span._meta.pop(SESSION_ID, None)

        return {
            "trace_id": "{:x}".format(span.trace_id),
            "span_id": str(span.span_id),
            "parent_id": str(self._get_llmobs_parent_id(span) or "undefined"),
            "session_id": session_id,
            "name": span.name,
            "tags": tags,
            "start_ns": span.start_ns,
            "duration": span.duration_ns,
            "error": span.error,
            "meta": meta,
            "metrics": metrics,
        }

    def _llmobs_tags(self, span: Span) -> List[str]:
        ml_app = config._llmobs_ml_app or "unnamed-ml-app"
        if span.get_tag(ML_APP):
            ml_app = span._meta.pop(ML_APP)
        tags = [
            "version:{}".format(config.version or ""),
            "env:{}".format(config.env or ""),
            "service:{}".format(span.service or ""),
            "source:integration",
            "ml_app:{}".format(ml_app),
            "session_id:{}".format(self._get_session_id(span)),
            "ddtrace.version:{}".format(ddtrace.__version__),
            "error:%d" % span.error,
        ]
        err_type = span.get_tag(ERROR_TYPE)
        if err_type:
            tags.append("error_type:%s" % err_type)
        existing_tags = span.get_tag(TAGS)
        if existing_tags is not None:
            span_tags = json.loads(existing_tags)
            tags.extend(["{}:{}".format(k, v) for k, v in span_tags.items()])
        return tags

    def _get_session_id(self, span: Span) -> str:
        """
        Return the session ID for a given span, in priority order:
        1) Span's session ID tag (if set manually)
        2) Session ID from the span's nearest LLMObs span ancestor
        3) Span's trace ID if no session ID is found
        """
        session_id = span.get_tag(SESSION_ID)
        if not session_id:
            nearest_llmobs_ancestor = self._get_nearest_llmobs_ancestor(span)
            if nearest_llmobs_ancestor:
                session_id = nearest_llmobs_ancestor.get_tag(SESSION_ID)
        return session_id or "{:x}".format(span.trace_id)

    def _get_llmobs_parent_id(self, span: Span) -> Optional[int]:
        """Return the span ID of the nearest LLMObs-type span in the span's ancestor tree."""
        nearest_llmobs_ancestor = self._get_nearest_llmobs_ancestor(span)
        if nearest_llmobs_ancestor:
            return nearest_llmobs_ancestor.span_id
        return None

    @staticmethod
    def _get_nearest_llmobs_ancestor(span: Span) -> Optional[Span]:
        """Return the nearest LLMObs-type ancestor span of a given span."""
        if span.span_type != SpanTypes.LLM:
            return None
        parent = span._parent
        while parent:
            if parent.span_type == SpanTypes.LLM:
                return parent
            parent = parent._parent
        return None
