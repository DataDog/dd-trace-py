import os
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

import ddtrace
from ddtrace import Span
from ddtrace import config
from ddtrace.internal import atexit
from ddtrace.internal.logger import get_logger
from ddtrace._trace.processor import TraceProcessor
from ddtrace.internal.llmobs.writer import LLMObsWriter
from ddtrace.internal.service import Service

log = get_logger(__name__)


class LLMObsSpan:
    def __init__(self, operation_kind, span, session_id=None):
        self._ddspan = span
        self.kind = operation_kind
        self.session_id = None
        self.input = None
        self.output = None
        self._llmobs_parent_id = None

    def set_input(self, input):
        self._ddspan.set_tag_str("ml_obs.input", input)
        self.input = input

    def set_output(self, output):
        self._ddspan.set_tag_str("ml_obs.output", output)
        self.output = output

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type:
                self._ddspan.set_exc_info(exc_type, exc_val, exc_tb)
            self.finish()
        except Exception:
            log.exception("error closing span")

    def finish(self):
        self._ddspan.finish()


        # TODO: write to LLMObs
        #  (note: we need to propagate the nearest LLMObs parent span in the ancestor tree)
        pass


class LLMObs(Service):
    _instance = None
    enabled = False

    def __init__(self, tracer=None):
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

    # def _start_span(self, operation_kind, name=None, session_id=None, **kwargs):
    #     if name is None:
    #         name = operation_kind
    #     span = self.tracer.trace(name, resource=operation_kind, span_type="LLMOBS")
    #     for k, v in kwargs.items():
    #         span.set_tag_str("ml_obs.span.{}".format(str(k)), str(v))
    #
    #     llmobs_span = LLMObsSpan(operation_kind, span, session_id=session_id)
    #     self.last_active_llmobs_span = llmobs_span
    #     return llmobs_span
    #
    # def agent(self, name=None):
    #     return self._start_span("agent", name=name)
    #
    # def workflow(self, name=None):
    #     return self._start_span("workflow", name=name)
    #
    # def tool(self, name=None):
    #     return self._start_span("tool", name=name)
    #
    # def task(self, name=None):
    #     return self._start_span("task", name=name)
    #
    # def llm(self, model, name=None, model_provider=None):
    #     if not model:
    #         raise ValueError("model must be the specified name of the model to invoke")
    #     if model_provider is None:
    #         model_provider = "custom"
    #     return self._start_span("llm", model=model, name=name, model_provider=model_provider)
    #
    # def annotate(
    #         self,
    #         span: LLMObsSpan = None,
    #         input: Union[str, Dict[str, str]] = None,
    #         output: Union[str, Dict[str, str]] = None,
    # ) -> None:
    #     """
    #     Annotate a span with input and/or output.
    #     If a span is not provided, the last active span will be annotated.
    #     """
    #
    #     if span is None:
    #         # FIXME: this doesn't really work. We need to figure out some way to keep a reference to the last active LLMObs Span.
    #         span = self.last_active_llmobs_span
    #     if span._ddspan.finished:
    #         log.warning("cannot annotate a finished span")
    #         return
    #     if input is not None:
    #         span.set_input(input)
    #     if output is not None:
    #         span.set_output(output)


class LLMObsTraceProcessor(TraceProcessor):
    """Processor that marks all spans in a trace that have an LLM-type span.

    1. If a span in a trace is an LLM-type span, we'll write the whole trace to LLMObs.
    2. any leaf spans in this trace will be marked as task-type spans.
    3. any non-leaf spans in this trace will be marked as chain/workflow-type spans.

    # FIXME: handle distributed cases (need to propagate LLMObs status in the response)
    """

    def __init__(self, llmobs_writer):
        self._writer = llmobs_writer

    def process_trace(self, trace):
        # type: (List[Span]) -> Optional[List[Span]]
        if trace:
            trace_contains_llm = False
            for span in trace:
                if span.span_type == "LLMOBS":
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

    def _has_children(self, trace, span):
        for s in trace:
            if s.parent_id == span.span_id:
                return True
        return False
