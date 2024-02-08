import os
from typing import List
from typing import Optional

import ddtrace
from ddtrace import Span
from ddtrace import config
from ddtrace.ext import SpanTypes
from ddtrace.internal import atexit
from ddtrace.internal.logger import get_logger
from ddtrace._trace.processor import TraceProcessor
from ddtrace.internal.llmobs.writer import LLMObsWriter
from ddtrace.internal.service import Service

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
            app_key=config._dd_app_key,
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
        if not config._dd_app_key:
            cls.enabled = False
            raise ValueError("DD_APP_KEY is required for sending LLMObs data")

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

    # FIXME: handle distributed cases (need to propagate LLMObs status in the response)
    """

    def __init__(self, llmobs_writer):
        self._writer = llmobs_writer

    def process_trace(self, trace: List[Span]) -> Optional[List[Span]]:
        if trace:
            trace_contains_llm = False
            for span in trace:
                if span.span_type == SpanTypes.LLMOBS:
                    trace_contains_llm = True
            if not trace_contains_llm:
                return trace

        for span in trace:
            if self._has_children(trace, span):
                span.set_tag_str("ml_obs.span_type", "chain")
            else:
                span.set_tag_str("ml_obs.span_type", "task")

        # TODO: Need to submit to LLMObsWriter, need to implement that as well

        return None

    @staticmethod
    def _has_children(trace: List[Span], span: Span) -> bool:
        for s in trace:
            if s.parent_id == span.span_id:
                return True
        return False
