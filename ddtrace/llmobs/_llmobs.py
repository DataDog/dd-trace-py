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
from ddtrace.constants import ERROR_TYPE
from ddtrace.ext import SpanTypes
from ddtrace.internal import atexit
from ddtrace.internal.logger import get_logger
from ddtrace.internal.service import Service
from ddtrace.llmobs._writer import LLMObsWriter
from ddtrace.llmobs._constants import TAGS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import SESSION_ID
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import INPUT_PARAMETERS
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import OUTPUT_VALUE


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
    ) -> Span:
        if cls.enabled is False:
            raise Exception("LLMObs.llm() cannot be used while LLMObs is disabled.")
        if not model_name:
            raise ValueError("model_name must be the specified name of the invoked model.")
        if model_provider is None:
            model_provider = "custom"
        return cls._instance._start_span(
            "llm", name, model_name=model_name, model_provider=model_provider, session_id=session_id
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
    def annotate(
        cls,
        span: Optional[Span] = None,
        parameters: Optional[Dict[str, Any]] = None,
        input_data: Optional[Union[List[Dict[str, Any]], str]] = None,
        output_data: Optional[Union[List[Dict[str, Any]], str]] = None,
        tags: Optional[Dict[str, Any]] = None,
    ):
        """
        Sets the parameter, input, and output tags for a given LLMObs span.
        span: Span to annotate. If no span is provided, the current active span will be used.
         Must be an LLMObs-type span.
        parameters: Dictionary of input parameter key-value pairs such as max_tokens/temperature.
         Will be mapped to span's meta.input.parameters.* fields.
        input_data: A single input string, or a list of dictionaries following {"content": "...", "role": "..."}.
         Will be mapped to `meta.input.value` or `meta.input.messages.*`, respectively.
         For llm span types, string inputs will be wrapped in a message dictionary.
        output_data: A single output string, or a list of dictionaries following {"content": "...", "role": "..."}.
         Will be mapped to `meta.output.value` or `meta.output.messages.*`, respectively.
         For llm span types, string outputs will be wrapped in a message dictionary.
        tags: Dictionary of key-value pairs to tag the LLMObs span with.
        """
        if cls.enabled is False:
            raise Exception("LLMObs.annotate() cannot be used while LLMObs is disabled.")
        if span is None:
            span = cls._instance.tracer.current_span()
            if span is None:
                raise ValueError("No span provided and no active span found.")
        if span.span_type != SpanTypes.LLM:
            raise ValueError("span must be an LLM-type span.")
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

    @staticmethod
    def _tag_params(span: Span, params: Dict[str, Any]) -> None:
        if not isinstance(params, dict):
            raise ValueError("parameters must be a dictionary of key-value pairs.")
        if not span.get_tag(INPUT_PARAMETERS):
            span.set_tag_str(INPUT_PARAMETERS, json.dumps(params))
        else:
            existing_params = json.loads(span.get_tag(INPUT_PARAMETERS))
            existing_params.update(params)
            span.set_tag_str(INPUT_PARAMETERS, json.dumps(existing_params))

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
            raise TypeError(
                "Invalid type, must be either a raw string or list of dictionaries with the format {'content': '...'}."
            )
        if isinstance(data, list) and not all(isinstance(item, dict) for item in data):
            raise TypeError(
                "Invalid list item type, must be a list of dictionaries with the format {'content': '...'}."
            )
        span_kind = span.get_tag(SPAN_KIND)
        tags = {}
        if not span_kind:
            raise ValueError("Cannot tag input/output without a span.kind tag.")
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
                raise ValueError("Invalid input/output type for non-llm span. Must be a raw string.")

    @staticmethod
    def _tag_span_tags(span: Span, span_tags: Dict[str, Any]) -> None:
        """Tags a given LLMObs span with a dictionary of key-value pairs."""
        if not isinstance(span_tags, dict):
            raise ValueError("span_tags must be a dictionary of string key - primitive value pairs.")
        span.set_tag_str(TAGS, json.dumps(span_tags))


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
        meta = {"span.kind": span._meta.pop(SPAN_KIND), "input": {}, "output": {}}
        session_id = span._meta.pop(SESSION_ID, None) or "{:x}".format(span.trace_id)
        if span.get_tag(MODEL_NAME):
            meta["model_name"] = span._meta.pop(MODEL_NAME)
            meta["model_provider"] = span._meta.pop(MODEL_PROVIDER, "custom")
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
        if not meta["input"]:
            meta.pop("input")
        if not meta["output"]:
            meta.pop("output")
        metrics = json.loads(span._meta.pop(METRICS, "{}"))

        return {
            "trace_id": "{:x}".format(span.trace_id),
            "span_id": str(span.span_id),
            "parent_id": str(self._get_llmobs_parent_id(span) or ""),
            "session_id": session_id,
            "name": span.name,
            "tags": tags,
            "start_ns": span.start_ns,
            "duration": span.duration_ns,
            "error": span.error,
            "meta": meta,
            "metrics": metrics,
        }

    @staticmethod
    def _llmobs_tags(span: Span) -> List[str]:
        tags = [
            "version:%s" % (config.version or ""),
            "env:%s" % (config.env or ""),
            "service:%s" % (span.service or ""),
            "source:integration",
            "model_name:{}".format(span.get_tag(MODEL_NAME) or ""),
            "model_provider:{}".format(span.get_tag(MODEL_PROVIDER) or "custom"),
            "error:%d" % span.error,
        ]
        err_type = span.get_tag(ERROR_TYPE)
        if err_type:
            tags.append("error_type:%s" % err_type)
        if span.get_tag(TAGS):
            span_tags = json.loads(span.get_tag(TAGS))
            tags.extend(["{}:{}".format(k, v) for k, v in span_tags.items()])
        return tags

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
