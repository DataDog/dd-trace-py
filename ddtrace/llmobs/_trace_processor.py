import json
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

import ddtrace
from ddtrace import Span
from ddtrace import config
from ddtrace._trace.processor import TraceProcessor
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE
from ddtrace.ext import SpanTypes
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import INPUT_DOCUMENTS
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import INPUT_PARAMETERS
from ddtrace.llmobs._constants import INPUT_PROMPT
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import ML_APP
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_DOCUMENTS
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import PARENT_ID_KEY
from ddtrace.llmobs._constants import RAGAS_ML_APP_PREFIX
from ddtrace.llmobs._constants import RUNNER_IS_INTEGRATION_SPAN_TAG
from ddtrace.llmobs._constants import SESSION_ID
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import TAGS
from ddtrace.llmobs._utils import _get_llmobs_parent_id
from ddtrace.llmobs._utils import _get_ml_app
from ddtrace.llmobs._utils import _get_session_id
from ddtrace.llmobs._utils import _get_span_name


log = get_logger(__name__)


class LLMObsTraceProcessor(TraceProcessor):
    """
    Processor that extracts LLM-type spans in a trace to submit as separate LLMObs span events to LLM Observability.
    """

    def __init__(self, llmobs_span_writer, evaluator_runner=None):
        self._span_writer = llmobs_span_writer
        self._evaluator_runner = evaluator_runner

    def process_trace(self, trace: List[Span]) -> Optional[List[Span]]:
        if not trace:
            return None
        for span in trace:
            if span.span_type == SpanTypes.LLM:
                self.submit_llmobs_span(span)
        return None if config._llmobs_agentless_enabled else trace

    def submit_llmobs_span(self, span: Span) -> None:
        """Generate and submit an LLMObs span event to be sent to LLMObs."""
        span_event = None
        is_llm_span = span.get_tag(SPAN_KIND) == "llm"
        is_ragas_integration_span = False
        try:
            span_event, is_ragas_integration_span = self._llmobs_span_event(span)
            self._span_writer.enqueue(span_event)
        except (KeyError, TypeError):
            log.error("Error generating LLMObs span event for span %s, likely due to malformed span", span)
        finally:
            if not span_event or not is_llm_span or is_ragas_integration_span:
                return
            if self._evaluator_runner:
                self._evaluator_runner.enqueue(span_event, span)

    def _llmobs_span_event(self, span: Span) -> Tuple[Dict[str, Any], bool]:
        """Span event object structure."""
        span_kind = span._meta.pop(SPAN_KIND)
        meta: Dict[str, Any] = {"span.kind": span_kind, "input": {}, "output": {}}
        if span_kind in ("llm", "embedding") and span.get_tag(MODEL_NAME) is not None:
            meta["model_name"] = span._meta.pop(MODEL_NAME)
            meta["model_provider"] = span._meta.pop(MODEL_PROVIDER, "custom").lower()
        if span.get_tag(METADATA) is not None:
            meta["metadata"] = json.loads(span._meta.pop(METADATA))
        if span.get_tag(INPUT_PARAMETERS):
            meta["input"]["parameters"] = json.loads(span._meta.pop(INPUT_PARAMETERS))
        if span_kind == "llm" and span.get_tag(INPUT_MESSAGES) is not None:
            meta["input"]["messages"] = json.loads(span._meta.pop(INPUT_MESSAGES))
        if span.get_tag(INPUT_VALUE) is not None:
            meta["input"]["value"] = span._meta.pop(INPUT_VALUE)
        if span_kind == "llm" and span.get_tag(OUTPUT_MESSAGES) is not None:
            meta["output"]["messages"] = json.loads(span._meta.pop(OUTPUT_MESSAGES))
        if span_kind == "embedding" and span.get_tag(INPUT_DOCUMENTS) is not None:
            meta["input"]["documents"] = json.loads(span._meta.pop(INPUT_DOCUMENTS))
        if span.get_tag(OUTPUT_VALUE) is not None:
            meta["output"]["value"] = span._meta.pop(OUTPUT_VALUE)
        if span_kind == "retrieval" and span.get_tag(OUTPUT_DOCUMENTS) is not None:
            meta["output"]["documents"] = json.loads(span._meta.pop(OUTPUT_DOCUMENTS))
        if span.get_tag(INPUT_PROMPT) is not None:
            prompt_json_str = span._meta.pop(INPUT_PROMPT)
            if span_kind != "llm":
                log.warning(
                    "Dropping prompt on non-LLM span kind, annotating prompts is only supported for LLM span kinds."
                )
            else:
                meta["input"]["prompt"] = json.loads(prompt_json_str)
        if span.error:
            meta[ERROR_MSG] = span.get_tag(ERROR_MSG)
            meta[ERROR_STACK] = span.get_tag(ERROR_STACK)
            meta[ERROR_TYPE] = span.get_tag(ERROR_TYPE)
        if not meta["input"]:
            meta.pop("input")
        if not meta["output"]:
            meta.pop("output")
        metrics = json.loads(span._meta.pop(METRICS, "{}"))
        ml_app = _get_ml_app(span)

        is_ragas_integration_span = False

        if ml_app.startswith(RAGAS_ML_APP_PREFIX):
            is_ragas_integration_span = True

        span.set_tag_str(ML_APP, ml_app)

        parent_id = str(_get_llmobs_parent_id(span) or "undefined")
        span._meta.pop(PARENT_ID_KEY, None)

        llmobs_span_event = {
            "trace_id": "{:x}".format(span.trace_id),
            "span_id": str(span.span_id),
            "parent_id": parent_id,
            "name": _get_span_name(span),
            "start_ns": span.start_ns,
            "duration": span.duration_ns,
            "status": "error" if span.error else "ok",
            "meta": meta,
            "metrics": metrics,
        }
        session_id = _get_session_id(span)
        if session_id is not None:
            span.set_tag_str(SESSION_ID, session_id)
            llmobs_span_event["session_id"] = session_id

        llmobs_span_event["tags"] = self._llmobs_tags(
            span, ml_app, session_id, is_ragas_integration_span=is_ragas_integration_span
        )
        return llmobs_span_event, is_ragas_integration_span

    @staticmethod
    def _llmobs_tags(
        span: Span, ml_app: str, session_id: Optional[str] = None, is_ragas_integration_span: bool = False
    ) -> List[str]:
        tags = {
            "version": config.version or "",
            "env": config.env or "",
            "service": span.service or "",
            "source": "integration",
            "ml_app": ml_app,
            "ddtrace.version": ddtrace.__version__,
            "language": "python",
            "error": span.error,
        }
        err_type = span.get_tag(ERROR_TYPE)
        if err_type:
            tags["error_type"] = err_type
        if session_id:
            tags["session_id"] = session_id
        if is_ragas_integration_span:
            tags[RUNNER_IS_INTEGRATION_SPAN_TAG] = "ragas"
        existing_tags = span._meta.pop(TAGS, None)
        if existing_tags is not None:
            tags.update(json.loads(existing_tags))
        return ["{}:{}".format(k, v) for k, v in tags.items()]
