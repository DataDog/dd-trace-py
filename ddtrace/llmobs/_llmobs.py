import json
import os
from typing import Any
from typing import Dict
from typing import Optional
from typing import Union

import ddtrace
from ddtrace import Span
from ddtrace import config
from ddtrace.ext import SpanTypes
from ddtrace.internal import atexit
from ddtrace.internal.logger import get_logger
from ddtrace.internal.service import Service
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import INPUT_PARAMETERS
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import ML_APP
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import SESSION_ID
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import TAGS
from ddtrace.llmobs._trace_processor import LLMObsTraceProcessor
from ddtrace.llmobs._utils import _get_ml_app
from ddtrace.llmobs._utils import _get_session_id
from ddtrace.llmobs._writer import LLMObsEvalMetricWriter
from ddtrace.llmobs._writer import LLMObsSpanWriter
from ddtrace.llmobs.utils import ExportedLLMObsSpan
from ddtrace.llmobs.utils import Messages


log = get_logger(__name__)


class LLMObs(Service):
    _instance = None
    enabled = False

    def __init__(self, tracer=None):
        super(LLMObs, self).__init__()
        self.tracer = tracer or ddtrace.tracer
        self._llmobs_span_writer = LLMObsSpanWriter(
            site=config._dd_site,
            api_key=config._dd_api_key,
            interval=float(os.getenv("_DD_LLMOBS_WRITER_INTERVAL", 1.0)),
            timeout=float(os.getenv("_DD_LLMOBS_WRITER_TIMEOUT", 2.0)),
        )
        self._llmobs_eval_metric_writer = LLMObsEvalMetricWriter(
            site=config._dd_site,
            api_key=config._dd_api_key,
            interval=float(os.getenv("_DD_LLMOBS_WRITER_INTERVAL", 1.0)),
            timeout=float(os.getenv("_DD_LLMOBS_WRITER_TIMEOUT", 2.0)),
        )
        self._llmobs_span_writer.start()
        self._llmobs_eval_metric_writer.start()

    def _start_service(self) -> None:
        tracer_filters = self.tracer._filters
        if not any(isinstance(tracer_filter, LLMObsTraceProcessor) for tracer_filter in tracer_filters):
            tracer_filters += [LLMObsTraceProcessor(self._llmobs_span_writer)]
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

    @classmethod
    def export_span(cls, span: Optional[Span] = None) -> Optional[ExportedLLMObsSpan]:
        """Returns a simple representation of a span to export its span and trace IDs.
        If no span is provided, the current active LLMObs-type span will be used.
        """
        if cls.enabled is False or cls._instance is None:
            log.warning("LLMObs.export_span() requires LLMObs to be enabled.")
            return None
        if span:
            try:
                if span.span_type != SpanTypes.LLM:
                    log.warning("Span must be an LLMObs-generated span.")
                    return None
                return ExportedLLMObsSpan(span_id=str(span.span_id), trace_id="{:x}".format(span.trace_id))
            except (TypeError, AttributeError):
                log.warning("Failed to export span. Span must be a valid Span object.")
                return None
        span = cls._instance.tracer.current_span()
        if span is None:
            log.warning("No span provided and no active LLMObs-generated span found.")
            return None
        if span.span_type != SpanTypes.LLM:
            log.warning("Span must be an LLMObs-generated span.")
            return None
        return ExportedLLMObsSpan(span_id=str(span.span_id), trace_id="{:x}".format(span.trace_id))

    def _start_span(
        self,
        operation_kind: str,
        name: Optional[str] = None,
        session_id: Optional[str] = None,
        model_name: Optional[str] = None,
        model_provider: Optional[str] = None,
        ml_app: Optional[str] = None,
    ) -> Span:
        if name is None:
            name = operation_kind
        span = self.tracer.trace(name, resource=operation_kind, span_type=SpanTypes.LLM)
        span.set_tag_str(SPAN_KIND, operation_kind)
        if model_name is not None:
            span.set_tag_str(MODEL_NAME, model_name)
        if model_provider is not None:
            span.set_tag_str(MODEL_PROVIDER, model_provider)
        if session_id is None:
            session_id = _get_session_id(span)
        span.set_tag_str(SESSION_ID, session_id)
        if ml_app is None:
            ml_app = _get_ml_app(span)
        span.set_tag_str(ML_APP, ml_app)
        return span

    @classmethod
    def llm(
        cls,
        model_name: str,
        name: Optional[str] = None,
        model_provider: Optional[str] = None,
        session_id: Optional[str] = None,
        ml_app: Optional[str] = None,
    ) -> Optional[Span]:
        """
        Trace an invocation call to an LLM where inputs and outputs are represented as text.

        :param str model_name: The name of the invoked LLM.
        :param str name: The name of the traced operation. If not provided, a default value of "llm" will be set.
        :param str model_provider: The name of the invoked LLM provider (ex: openai, bedrock).
                                   If not provided, a default value of "custom" will be set.
        :param str session_id: The ID of the underlying user session. Required for tracking sessions.
        :param str ml_app: The name of the ML application that the agent is orchestrating. If not provided, the default
                           value DD_LLMOBS_APP_NAME will be set.

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
            "llm", name, model_name=model_name, model_provider=model_provider, session_id=session_id, ml_app=ml_app
        )

    @classmethod
    def tool(
        cls, name: Optional[str] = None, session_id: Optional[str] = None, ml_app: Optional[str] = None
    ) -> Optional[Span]:
        """
        Trace a call to an external interface or API.

        :param str name: The name of the traced operation. If not provided, a default value of "tool" will be set.
        :param str session_id: The ID of the underlying user session. Required for tracking sessions.
        :param str ml_app: The name of the ML application that the agent is orchestrating. If not provided, the default
                           value DD_LLMOBS_APP_NAME will be set.

        :returns: The Span object representing the traced operation.
        """
        if cls.enabled is False or cls._instance is None:
            log.warning("LLMObs.tool() cannot be used while LLMObs is disabled.")
            return None
        return cls._instance._start_span("tool", name=name, session_id=session_id, ml_app=ml_app)

    @classmethod
    def task(
        cls, name: Optional[str] = None, session_id: Optional[str] = None, ml_app: Optional[str] = None
    ) -> Optional[Span]:
        """
        Trace a standalone non-LLM operation which does not involve an external request.

        :param str name: The name of the traced operation. If not provided, a default value of "task" will be set.
        :param str session_id: The ID of the underlying user session. Required for tracking sessions.
        :param str ml_app: The name of the ML application that the agent is orchestrating. If not provided, the default
                           value DD_LLMOBS_APP_NAME will be set.

        :returns: The Span object representing the traced operation.
        """
        if cls.enabled is False or cls._instance is None:
            log.warning("LLMObs.task() cannot be used while LLMObs is disabled.")
            return None
        return cls._instance._start_span("task", name=name, session_id=session_id, ml_app=ml_app)

    @classmethod
    def agent(
        cls, name: Optional[str] = None, session_id: Optional[str] = None, ml_app: Optional[str] = None
    ) -> Optional[Span]:
        """
        Trace a dynamic workflow in which an embedded language model (agent) decides what sequence of actions to take.

        :param str name: The name of the traced operation. If not provided, a default value of "agent" will be set.
        :param str session_id: The ID of the underlying user session. Required for tracking sessions.
        :param str ml_app: The name of the ML application that the agent is orchestrating. If not provided, the default
                           value DD_LLMOBS_APP_NAME will be set.

        :returns: The Span object representing the traced operation.
        """
        if cls.enabled is False or cls._instance is None:
            log.warning("LLMObs.agent() cannot be used while LLMObs is disabled.")
            return None
        return cls._instance._start_span("agent", name=name, session_id=session_id, ml_app=ml_app)

    @classmethod
    def workflow(
        cls, name: Optional[str] = None, session_id: Optional[str] = None, ml_app: Optional[str] = None
    ) -> Optional[Span]:
        """
        Trace a predefined or static sequence of operations.

        :param str name: The name of the traced operation. If not provided, a default value of "workflow" will be set.
        :param str session_id: The ID of the underlying user session. Required for tracking sessions.
        :param str ml_app: The name of the ML application that the agent is orchestrating. If not provided, the default
                           value DD_LLMOBS_APP_NAME will be set.

        :returns: The Span object representing the traced operation.
        """
        if cls.enabled is False or cls._instance is None:
            log.warning("LLMObs.workflow() cannot be used while LLMObs is disabled.")
            return None
        return cls._instance._start_span("workflow", name=name, session_id=session_id, ml_app=ml_app)

    @classmethod
    def annotate(
        cls,
        span: Optional[Span] = None,
        parameters: Optional[Dict[str, Any]] = None,
        input_data: Optional[Any] = None,
        output_data: Optional[Any] = None,
        metadata: Optional[Dict[str, Any]] = None,
        metrics: Optional[Dict[str, Any]] = None,
        tags: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Sets parameters, inputs, outputs, tags, and metrics as provided for a given LLMObs span.
        Note that with the exception of tags, this method will override any existing values for the provided fields.

        :param Span span: Span to annotate. If no span is provided, the current active span will be used.
                          Must be an LLMObs-type span, i.e. generated by the LLMObs SDK.
        :param input_data: A single input string, dictionary, or a list of dictionaries based on the span kind:
                           - llm spans: accepts a string, or a dictionary of form {"content": "...", "role": "..."},
                                        or a list of dictionaries with the same signature.
                           - other: any JSON serializable type.
        :param output_data: A single output string, dictionary, or a list of dictionaries based on the span kind:
                           - llm spans: accepts a string, or a dictionary of form {"content": "...", "role": "..."},
                                        or a list of dictionaries with the same signature.
                           - other: any JSON serializable type.
        :param parameters: (DEPRECATED) Dictionary of JSON serializable key-value pairs to set as input parameters.
        :param metadata: Dictionary of JSON serializable key-value metadata pairs relevant to the input/output operation
                         described by the LLMObs span.
        :param tags: Dictionary of JSON serializable key-value tag pairs to set or update on the LLMObs span
                     regarding the span's context.
        :param metrics: Dictionary of JSON serializable key-value metric pairs,
                        such as `{prompt,completion,total}_tokens`.
        """
        if cls.enabled is False or cls._instance is None:
            log.warning("LLMObs.annotate() cannot be used while LLMObs is disabled.")
            return
        if span is None:
            span = cls._instance.tracer.current_span()
            if span is None:
                log.warning("No span provided and no active LLMObs-generated span found.")
                return
        if span.span_type != SpanTypes.LLM:
            log.warning("Span must be an LLMObs-generated span.")
            return
        if span.finished:
            log.warning("Cannot annotate a finished span.")
            return
        span_kind = span.get_tag(SPAN_KIND)
        if not span_kind:
            log.warning("LLMObs span must have a span kind specified.")
            return
        if parameters is not None:
            log.warning("Setting parameters is deprecated, please set parameters and other metadata as tags instead.")
            cls._tag_params(span, parameters)
        if input_data or output_data:
            if span_kind == "llm":
                cls._tag_llm_io(span, input_messages=input_data, output_messages=output_data)
            else:
                cls._tag_text_io(span, input_value=input_data, output_value=output_data)
        if metadata is not None:
            cls._tag_metadata(span, metadata)
        if metrics is not None:
            cls._tag_metrics(span, metrics)
        if tags is not None:
            cls._tag_span_tags(span, tags)

    @staticmethod
    def _tag_params(span: Span, params: Dict[str, Any]) -> None:
        """Tags input parameters for a given LLMObs span.
        Will be mapped to span's `meta.input.parameters` field.
        """
        if not isinstance(params, dict):
            log.warning("parameters must be a dictionary of key-value pairs.")
            return
        try:
            span.set_tag_str(INPUT_PARAMETERS, json.dumps(params))
        except TypeError:
            log.warning("Failed to parse input parameters. Parameters must be JSON serializable.")

    @classmethod
    def _tag_llm_io(cls, span, input_messages=None, output_messages=None):
        """Tags input/output messages for LLM-kind spans.
        Will be mapped to span's `meta.{input,output}.messages` fields.
        """
        if input_messages is not None:
            try:
                if not isinstance(input_messages, Messages):
                    input_messages = Messages(input_messages)
                if input_messages.messages:
                    span.set_tag_str(INPUT_MESSAGES, json.dumps(input_messages.messages))
            except (TypeError, AttributeError):
                log.warning("Failed to parse input messages.", exc_info=True)
        if output_messages is not None:
            try:
                if not isinstance(output_messages, Messages):
                    output_messages = Messages(output_messages)
                if output_messages.messages:
                    span.set_tag_str(OUTPUT_MESSAGES, json.dumps(output_messages.messages))
            except (TypeError, AttributeError):
                log.warning("Failed to parse output messages.", exc_info=True)

    @classmethod
    def _tag_text_io(cls, span, input_value=None, output_value=None):
        """Tags input/output values for non-LLM kind spans.
        Will be mapped to span's `meta.{input,output}.values` fields.
        """
        if input_value is not None:
            if isinstance(input_value, str):
                span.set_tag_str(INPUT_VALUE, input_value)
            else:
                try:
                    span.set_tag_str(INPUT_VALUE, json.dumps(input_value))
                except TypeError:
                    log.warning("Failed to parse input value. Input value must be JSON serializable.")
        if output_value is not None:
            if isinstance(output_value, str):
                span.set_tag_str(OUTPUT_VALUE, output_value)
            else:
                try:
                    span.set_tag_str(OUTPUT_VALUE, json.dumps(output_value))
                except TypeError:
                    log.warning("Failed to parse output value. Output value must be JSON serializable.")

    @staticmethod
    def _tag_span_tags(span: Span, span_tags: Dict[str, Any]) -> None:
        """Tags a given LLMObs span with a dictionary of key-value tag pairs.
        If tags are already set on the span, the new tags will be merged with the existing tags.
        """
        if not isinstance(span_tags, dict):
            log.warning("span_tags must be a dictionary of string key - primitive value pairs.")
            return
        try:
            current_tags = span.get_tag(TAGS)
            if current_tags:
                span_tags.update(json.loads(current_tags))
            span.set_tag_str(TAGS, json.dumps(span_tags))
        except TypeError:
            log.warning("Failed to parse span tags. Tag key-value pairs must be JSON serializable.")

    @staticmethod
    def _tag_metadata(span: Span, metadata: Dict[str, Any]) -> None:
        """Tags a given LLMObs span with a dictionary of key-value metadata pairs."""
        if not isinstance(metadata, dict):
            log.warning("metadata must be a dictionary of string key-value pairs.")
            return
        try:
            span.set_tag_str(METADATA, json.dumps(metadata))
        except TypeError:
            log.warning("Failed to parse span metadata. Metadata key-value pairs must be JSON serializable.")

    @staticmethod
    def _tag_metrics(span: Span, metrics: Dict[str, Any]) -> None:
        """Tags a given LLMObs span with a dictionary of key-value metric pairs."""
        if not isinstance(metrics, dict):
            log.warning("metrics must be a dictionary of string key - numeric value pairs.")
            return
        try:
            span.set_tag_str(METRICS, json.dumps(metrics))
        except TypeError:
            log.warning("Failed to parse span metrics. Metric key-value pairs must be JSON serializable.")

    @classmethod
    def submit_evaluation(
        cls,
        span_context: Dict[str, str],
        label: str,
        metric_type: str,
        value: Union[str, int, float],
    ) -> None:
        """
        Submits a custom evaluation metric for a given span ID and trace ID.

        :param span_context: A dictionary containing the span_id and trace_id of interest.
        :param str label: The name of the evaluation metric.
        :param str metric_type: The type of the evaluation metric. One of "categorical", "numerical", and "score".
        :param value: The value of the evaluation metric.
                      Must be a string (categorical), integer (numerical/score), or float (numerical/score).
        """
        if cls.enabled is False or cls._instance is None or cls._instance._llmobs_eval_metric_writer is None:
            log.warning("LLMObs.submit_evaluation() requires LLMObs to be enabled.")
            return
        if not isinstance(span_context, dict):
            log.warning(
                "span_context must be a dictionary containing both span_id and trace_id keys. "
                "LLMObs.export_span() can be used to generate this dictionary from a given span."
            )
            return
        span_id = span_context.get("span_id")
        trace_id = span_context.get("trace_id")
        if not (span_id and trace_id):
            log.warning("span_id and trace_id must both be specified for the given evaluation metric to be submitted.")
            return
        if not label:
            log.warning("label must be the specified name of the evaluation metric.")
            return
        if not metric_type or metric_type.lower() not in ("categorical", "numerical", "score"):
            log.warning("metric_type must be one of 'categorical', 'numerical', or 'score'.")
            return
        if metric_type == "categorical" and not isinstance(value, str):
            log.warning("value must be a string for a categorical metric.")
            return
        if metric_type in ("numerical", "score") and not isinstance(value, (int, float)):
            log.warning("value must be an integer or float for a numerical/score metric.")
            return
        cls._instance._llmobs_eval_metric_writer.enqueue(
            {
                "span_id": span_id,
                "trace_id": trace_id,
                "label": str(label),
                "metric_type": metric_type.lower(),
                "{}_value".format(metric_type): value,
            }
        )
