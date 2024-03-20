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
from ddtrace.ext import SpanTypes
from ddtrace.internal import atexit
from ddtrace.internal.logger import get_logger
from ddtrace.internal.service import Service
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
from ddtrace.llmobs._trace_processor import LLMObsTraceProcessor
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
        ml_app: Optional[str] = None,
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
        if ml_app is not None:
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
        Trace an interaction with a large language model (LLM).

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
        Trace an operation of an interface/software used for interacting with or supporting an LLM.

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
        Trace an operation of a function/task that is part of a larger workflow involving an LLM.

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
        Trace a workflow orchestrated by an LLM agent.

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
        Trace a sequence of operations that are part of a larger workflow involving an LLM.

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
