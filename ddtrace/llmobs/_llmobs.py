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

    def _start_span(self, operation_kind: str, name: str = None, **kwargs) -> Span:
        if name is None:
            name = operation_kind
        span = self.tracer.trace(name, resource=operation_kind, span_type=SpanTypes.LLM)
        span_meta = {}
        for k, v in kwargs.items():
            span_meta.update({k: v})
        span_meta["span.kind"] = operation_kind
        span.set_tag_str("ml_obs.meta", json.dumps(span_meta))
        return span

    @classmethod
    def llm(
        cls,
        model: str,
        name: Optional[str] = None,
        model_provider: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Span:
        if cls.enabled is False:
            raise Exception("LLMObs.llm() cannot be used while LLMObs is disabled.")
        if not model:
            raise ValueError("model must be the specified name of the invoked model.")
        if model_provider is None:
            model_provider = "custom"
        return cls._instance._start_span(
            "llm", name, model_name=model, model_provider=model_provider, session_id=session_id
        )

    @classmethod
    def tool(cls, name: Optional[str] = None, session_id: Optional[str] = None) -> Span:
        if cls.enabled is False:
            raise Exception("LLMObs.tool() cannot be used while LLMObs is disabled.")
        return cls._instance._start_span("tool", name=name, session_id=session_id)

    @classmethod
    def task(cls, name: Optional[str] = None, session_id: Optional[str] = None) -> Span:
        if cls.enabled is False:
            raise Exception("LLMObs.task() cannot be used while LLMObs is disabled.")
        return cls._instance._start_span("task", name=name, session_id=session_id)

    @classmethod
    def agent(cls, name: Optional[str] = None, session_id: Optional[str] = None) -> Span:
        if cls.enabled is False:
            raise Exception("LLMObs.agent() cannot be used while LLMObs is disabled.")
        return cls._instance._start_span("agent", name=name, session_id=session_id)

    @classmethod
    def workflow(cls, name: Optional[str] = None, session_id: Optional[str] = None) -> Span:
        if cls.enabled is False:
            raise Exception("LLMObs.workflow() cannot be used while LLMObs is disabled.")
        return cls._instance._start_span("workflow", name=name, session_id=session_id)

    @classmethod
    def tag(
        cls,
        span: Optional[Span] = None,
        parameters: Optional[Dict[str, Any]] = None,
        input_tags: Optional[Union[List[Dict[str, Any]], str]] = None,
        output_tags: Optional[Union[List[Dict[str, Any]], str]] = None,
    ):
        """
        Sets the parameter, input, and output tags for a given LLMObs span.
        span: Span to annotate. If no span is provided, the current active span will be used.
         Must be an LLMObs-type span.
        parameters: Dictionary of input parameter key-value pairs such as max_tokens/temperature.
         Will be mapped to span's meta.input.parameters.* fields.
        input_tags: A single input string, or a list of dictionaries following {"content": "...", "role": "..."}.
         Will be mapped to `meta.input.value` or `meta.input.messages.*`, respectively.
         For llm span types, string inputs will be wrapped in a message dictionary.
        output_tags: A single output string, or a list of dictionaries following {"content": "...", "role": "..."}.
         Will be mapped to `meta.output.value` or `meta.output.messages.*`, respectively.
         For llm span types, string outputs will be wrapped in a message dictionary.
        """
        if cls.enabled is False:
            raise Exception("LLMObs.tag() cannot be used while LLMObs is disabled.")
        if span is None:
            span = cls._instance.tracer.current_span()
            if span is None:
                raise ValueError("No span provided and no active span found.")
        if span.span_type != SpanTypes.LLM:
            raise ValueError("span must be an LLM-type span.")
        if span.finished:
            log.warning("Cannot annotate a finished span.")
            return
        try:
            span_meta = json.loads(span.get_tag("ml_obs.meta") or "{}")
        except json.JSONDecodeError:
            raise ValueError("Failed to deserialize existing ml_obs.meta tag.")
        if parameters is not None:
            span_meta = cls._tag_params(span_meta, parameters)
        if input_tags is not None:
            span_meta = cls._tag_span_input_output("input", span_meta, input_tags)
        if output_tags is not None:
            span_meta = cls._tag_span_input_output("output", span_meta, output_tags)
        try:
            span.set_tag_str("ml_obs.meta", json.dumps(span_meta))
        except json.JSONDecodeError:
            raise ValueError("Failed to serialize span meta.")

    @staticmethod
    def _tag_params(meta: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(params, dict):
            raise ValueError("parameters must be a dictionary of key-value pairs.")
        if "input" not in meta:
            meta["input"] = {"parameters": params}
        elif "parameters" not in meta["input"]:
            meta["input"]["parameters"] = params
        elif "parameters" in meta["input"]:
            meta["input"]["parameters"].update(params)
        return meta

    @staticmethod
    def _tag_span_input_output(
        io_type: str, meta: Dict[str, Any], io_tags: Union[List[Dict[str, Any]], str]
    ) -> Dict[str, Any]:
        """
        Tags input/output for a given LLMObs span.
        io_type: "input" or "output".
        meta: Span's meta dictionary.
        io_value: Input or output dictionary of key-value pairs to tag.
        """
        if io_type not in ("input", "output"):
            raise ValueError("io_type must be either 'input' or 'output'.")
        if not isinstance(io_tags, (str, list)):
            raise TypeError(
                "Invalid type, must be either a raw string or list of dictionaries with the format {'content': '...'}."
            )
        if isinstance(io_tags, list) and not all(isinstance(item, dict) for item in io_tags):
            raise TypeError(
                "Invalid list item type, must be a list of dictionaries with the format {'content': '...'}."
            )
        span_kind = meta.get("span.kind")
        tags = {}
        if not span_kind:
            raise ValueError("Cannot tag input/output without a span.kind tag.")
        if span_kind == "llm":
            if isinstance(io_tags, str):
                tags = {"messages": [{"content": io_tags}]}
            elif isinstance(io_tags, list) and io_tags and isinstance(io_tags[0], dict):
                tags = {"messages": io_tags}
        else:
            if isinstance(io_tags, str):
                tags = {"value": io_tags}
            else:
                raise ValueError("Invalid input/output type for non-llm span. Must be a raw string.")
        if io_type not in meta:
            meta[io_type] = tags
        else:
            meta[io_type].update(tags)
        return meta


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
        if "span.kind" not in meta:
            meta["span.kind"] = span.resource
        if span.error:
            meta["error.message"] = span.get_tag("error.message")
        metrics = json.loads(span._meta.pop("ml_obs.metrics", "{}"))
        span_event = self._llmobs_span_event(span, meta, metrics)
        log.debug("Submitting span to LLMObs: %s", span)
        self._writer.enqueue(span_event)

    def _llmobs_span_event(self, span: Span, meta: Dict[str, Any], metrics: Dict[str, Any]) -> Dict[str, Any]:
        """Span event object structure."""
        session_id = meta.pop("session_id") or "{:x}".format(span.trace_id)
        return {
            "trace_id": "{:x}".format(span.trace_id),
            "span_id": str(span.span_id),
            "parent_id": str(self._get_llmobs_parent_id(span) or ""),
            "session_id": session_id,
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
