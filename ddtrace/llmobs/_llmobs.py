import json
import os
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

import ddtrace
from ddtrace import Span
from ddtrace import config
from ddtrace._trace.processor import TraceProcessor
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE
from ddtrace.ext import SpanTypes
from ddtrace.internal import atexit
from ddtrace.internal.logger import get_logger
from ddtrace.internal.service import Service
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import INPUT_PARAMETERS
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import SESSION_ID
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import TAGS
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
            raise ValueError(
                "DD_API_KEY is required for sending LLMObs data. "
                "Ensure this configuration is set before running your application."
            )
        if not config._llmobs_ml_app:
            cls.enabled = False
            raise ValueError(
                "DD_LLMOBS_APP_NAME is required for sending LLMObs data. "
                "Ensure this configuration is set before running your application."
            )

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

    def _start_span(
        self,
        operation_kind: str,
        name: Optional[str] = None,
        session_id: Optional[str] = None,
        model_name: Optional[str] = None,
        model_provider: Optional[str] = None,
    ) -> Span:
        if name is None:
            name = operation_kind
        span = self.tracer.trace(name, resource=operation_kind, span_type=SpanTypes.LLM)
        span.set_tag_str(SPAN_KIND, operation_kind)
        if session_id is not None:
            span.set_tag_str(SESSION_ID, session_id)
        if model_name is not None:
            span.set_tag_str(MODEL_NAME, model_name)
        if model_provider is not None:
            span.set_tag_str(MODEL_PROVIDER, model_provider)
        return span

    @classmethod
    def llm(
        cls,
        model_name: str,
        name: Optional[str] = None,
        model_provider: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Optional[Span]:
        """
        Trace an interaction with a large language model (LLM).

        :param str model_name: The name of the invoked LLM.
        :param str name: The name of the traced operation. If not provided, a default value of "llm" will be set.
        :param str model_provider: The name of the invoked LLM provider (ex: openai, bedrock).
                                   If not provided, a default value of "custom" will be set.
        :param str session_id: The ID of the underlying user session. Required for tracking sessions.

        :returns: The Span object representing the traced operation.
        """
        if cls.enabled is False or cls._instance is None:
            log.warning("LLMObs.llm() cannot be used while LLMObs is disabled.")
            return None
        if not model_name:
            log.warning("model_name must be the specified name of the invoked model.")
            return None
        if model_provider is None:
            model_provider = "custom"
        return cls._instance._start_span(
            "llm", name, model_name=model_name, model_provider=model_provider, session_id=session_id
        )

    @classmethod
    def tool(cls, name: Optional[str] = None, session_id: Optional[str] = None) -> Optional[Span]:
        """
        Trace an operation of an interface/software used for interacting with or supporting an LLM.

        :param str name: The name of the traced operation. If not provided, a default value of "tool" will be set.
        :param str session_id: The ID of the underlying user session. Required for tracking sessions.

        :returns: The Span object representing the traced operation.
        """
        if cls.enabled is False or cls._instance is None:
            log.warning("LLMObs.tool() cannot be used while LLMObs is disabled.")
            return None
        return cls._instance._start_span("tool", name=name, session_id=session_id)

    @classmethod
    def task(cls, name: Optional[str] = None, session_id: Optional[str] = None) -> Optional[Span]:
        """
        Trace an operation of a function/task that is part of a larger workflow involving an LLM.

        :param str name: The name of the traced operation. If not provided, a default value of "task" will be set.
        :param str session_id: The ID of the underlying user session. Required for tracking sessions.

        :returns: The Span object representing the traced operation.
        """
        if cls.enabled is False or cls._instance is None:
            log.warning("LLMObs.task() cannot be used while LLMObs is disabled.")
            return None
        return cls._instance._start_span("task", name=name, session_id=session_id)

    @classmethod
    def agent(cls, name: Optional[str] = None, session_id: Optional[str] = None) -> Optional[Span]:
        """
        Trace a workflow orchestrated by an LLM agent.

        :param str name: The name of the traced operation. If not provided, a default value of "agent" will be set.
        :param str session_id: The ID of the underlying user session. Required for tracking sessions.

        :returns: The Span object representing the traced operation.
        """
        if cls.enabled is False or cls._instance is None:
            log.warning("LLMObs.agent() cannot be used while LLMObs is disabled.")
            return None
        return cls._instance._start_span("agent", name=name, session_id=session_id)

    @classmethod
    def workflow(cls, name: Optional[str] = None, session_id: Optional[str] = None) -> Optional[Span]:
        """
        Trace a sequence of operations that are part of a larger workflow involving an LLM.

        :param str name: The name of the traced operation. If not provided, a default value of "workflow" will be set.
        :param str session_id: The ID of the underlying user session. Required for tracking sessions.

        :returns: The Span object representing the traced operation.
        """
        if cls.enabled is False or cls._instance is None:
            log.warning("LLMObs.workflow() cannot be used while LLMObs is disabled.")
            return None
        return cls._instance._start_span("workflow", name=name, session_id=session_id)

    @classmethod
    def annotate(
        cls,
        span: Optional[Span] = None,
        parameters: Optional[Dict[str, Any]] = None,
        input_data: Optional[Union[List[Dict[str, Any]], str]] = None,
        output_data: Optional[Union[List[Dict[str, Any]], str]] = None,
        tags: Optional[Dict[str, Any]] = None,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Sets the parameter, input, output, tags, and metrics for a given LLMObs span.
        Note that this method will override any existing values for the specified fields.

        :param Span span: Span to annotate. If no span is provided, the current active span will be used.
                          Must be an LLMObs-type span.
        :param parameters: Dictionary of input parameter key-value pairs such as max_tokens/temperature.
                           Will be mapped to span's meta.input.parameters.* fields.
        :param input_data: A single input string, or a list of dictionaries of form {"content": "...", "role": "..."}.
                           Will be mapped to `meta.input.value` or `meta.input.messages.*`, respectively.
                           For llm/agent spans, string inputs will be wrapped in a message dictionary.
        :param output_data: A single output string, or a list of dictionaries of form {"content": "...", "role": "..."}.
                            Will be mapped to `meta.output.value` or `meta.output.messages.*`, respectively.
                            For llm/agent spans, string outputs will be wrapped in a message dictionary.
        :param tags: Dictionary of key-value custom tag pairs to set on the LLMObs span.
        :param metrics: Dictionary of key-value metric pairs such as prompt_tokens/completion_tokens/total_tokens.
                        Will be mapped to span's metrics.* fields.
        """
        if cls.enabled is False or cls._instance is None:
            log.warning("LLMObs.annotate() cannot be used while LLMObs is disabled.")
            return
        if span is None:
            span = cls._instance.tracer.current_span()
            if span is None:
                log.warning("No span provided and no active span found.")
                return
        if span.span_type != SpanTypes.LLM:
            log.warning("Span must be an LLM-type span.")
            return
        if span.finished:
            log.warning("Cannot annotate a finished span.")
            return
        if parameters is not None:
            cls._tag_params(span, parameters)
        if input_data is not None:
            cls._tag_span_input_output("input", span, input_data)
        if output_data is not None:
            cls._tag_span_input_output("output", span, output_data)
        if tags is not None:
            cls._tag_span_tags(span, tags)
        if metrics is not None:
            cls._tag_metrics(span, metrics)

    @staticmethod
    def _tag_params(span: Span, params: Dict[str, Any]) -> None:
        """Tags input parameters for a given LLMObs span."""
        if not isinstance(params, dict):
            log.warning("parameters must be a dictionary of key-value pairs.")
            return
        span.set_tag_str(INPUT_PARAMETERS, json.dumps(params))

    @staticmethod
    def _tag_span_input_output(io_type: str, span: Span, data: Union[List[Dict[str, Any]], str]) -> None:
        """
        Tags input/output for a given LLMObs span.
        io_type: "input" or "output".
        meta: Span's meta dictionary.
        data: String or dictionary of key-value pairs to tag as the input or output.
        """
        if io_type not in ("input", "output"):
            raise ValueError("io_type must be either 'input' or 'output'.")
        if not isinstance(data, (str, list)):
            log.warning(
                "Invalid type, must be either a raw string or list of dictionaries with the format {'content': '...'}."
            )
            return
        if isinstance(data, list) and not all(isinstance(item, dict) for item in data):
            log.warning("Invalid list item type, must be a list of dictionaries with the format {'content': '...'}.")
            return
        span_kind = span.get_tag(SPAN_KIND)
        tags = None
        if not span_kind:
            log.warning("Cannot tag input/output without a span.kind tag.")
            return
        if span_kind in ("llm", "agent"):
            if isinstance(data, str):
                tags = [{"content": data}]
            elif isinstance(data, list) and data and isinstance(data[0], dict):
                tags = data
            if io_type == "input":
                span.set_tag_str(INPUT_MESSAGES, json.dumps(tags))
            else:
                span.set_tag_str(OUTPUT_MESSAGES, json.dumps(tags))
        else:
            if isinstance(data, str):
                if io_type == "input":
                    span.set_tag_str(INPUT_VALUE, data)
                else:
                    span.set_tag_str(OUTPUT_VALUE, data)
            else:
                log.warning("Invalid input/output type for non-llm span. Must be a raw string.")

    @staticmethod
    def _tag_span_tags(span: Span, span_tags: Dict[str, Any]) -> None:
        """Tags a given LLMObs span with a dictionary of key-value tag pairs."""
        if not isinstance(span_tags, dict):
            log.warning("span_tags must be a dictionary of string key - primitive value pairs.")
            return
        span.set_tag_str(TAGS, json.dumps(span_tags))

    @staticmethod
    def _tag_metrics(span: Span, metrics: Dict[str, Any]) -> None:
        """Tags a given LLMObs span with a dictionary of key-value metric pairs."""
        if not isinstance(metrics, dict):
            log.warning("metrics must be a dictionary of string key - numeric value pairs.")
            return
        span.set_tag_str(METRICS, json.dumps(metrics))


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
            meta["error.message"] = span.get_tag(ERROR_MSG)
            meta["error.stack"] = span.get_tag(ERROR_STACK)
        if not meta["input"]:
            meta.pop("input")
        if not meta["output"]:
            meta.pop("output")
        metrics = json.loads(span._meta.pop(METRICS, "{}"))

        return {
            "trace_id": "{:x}".format(span.trace_id),
            "span_id": str(span.span_id),
            "parent_id": str(self._get_llmobs_parent_id(span) or "undefined"),
            "session_id": self._get_session_id(span),
            "name": span.name,
            "tags": tags,
            "start_ns": span.start_ns,
            "duration": span.duration_ns,
            "error": span.error,
            "meta": meta,
            "metrics": metrics,
        }

    def _llmobs_tags(self, span: Span) -> List[str]:
        tags = [
            "version:{}".format(config.version or ""),
            "env:{}".format(config.env or ""),
            "service:{}".format(span.service or ""),
            "source:integration",
            "ml_app:{}".format(config._llmobs_ml_app or "unnamed-ml-app"),
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
        session_id = span._meta.pop(SESSION_ID, None)
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
