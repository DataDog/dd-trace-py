import json
import os
import time
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

import ddtrace
from ddtrace import config
from ddtrace import patch
from ddtrace._trace.context import Context
from ddtrace._trace.span import Span
from ddtrace._trace.tracer import Tracer
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE
from ddtrace.ext import SpanTypes
from ddtrace.internal import atexit
from ddtrace.internal import core
from ddtrace.internal import forksafe
from ddtrace.internal._rand import rand64bits
from ddtrace.internal.compat import ensure_text
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig.worker import remoteconfig_poller
from ddtrace.internal.service import Service
from ddtrace.internal.service import ServiceStatusError
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_APM_PRODUCT
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.internal.utils.formats import parse_tags_str
from ddtrace.llmobs import _constants as constants
from ddtrace.llmobs import _telemetry as telemetry
from ddtrace.llmobs._constants import AGENT_MANIFEST
from ddtrace.llmobs._constants import ANNOTATIONS_CONTEXT_ID
from ddtrace.llmobs._constants import DECORATOR
from ddtrace.llmobs._constants import DISPATCH_ON_LLM_TOOL_CHOICE
from ddtrace.llmobs._constants import DISPATCH_ON_TOOL_CALL
from ddtrace.llmobs._constants import DISPATCH_ON_TOOL_CALL_OUTPUT_USED
from ddtrace.llmobs._constants import INPUT_DOCUMENTS
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import INPUT_PROMPT
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import INTEGRATION
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import ML_APP
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_DOCUMENTS
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import PARENT_ID_KEY
from ddtrace.llmobs._constants import PROPAGATED_PARENT_ID_KEY
from ddtrace.llmobs._constants import ROOT_PARENT_ID
from ddtrace.llmobs._constants import SESSION_ID
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import SPAN_LINKS
from ddtrace.llmobs._constants import SPAN_START_WHILE_DISABLED_WARNING
from ddtrace.llmobs._constants import TAGS
from ddtrace.llmobs._context import LLMObsContextProvider
from ddtrace.llmobs._evaluators.runner import EvaluatorRunner
from ddtrace.llmobs._utils import AnnotationContext
from ddtrace.llmobs._utils import LinkTracker
from ddtrace.llmobs._utils import ToolCallTracker
from ddtrace.llmobs._utils import _get_ml_app
from ddtrace.llmobs._utils import _get_session_id
from ddtrace.llmobs._utils import _get_span_name
from ddtrace.llmobs._utils import _is_evaluation_span
from ddtrace.llmobs._utils import safe_json
from ddtrace.llmobs._utils import validate_prompt
from ddtrace.llmobs._writer import LLMObsEvalMetricWriter
from ddtrace.llmobs._writer import LLMObsSpanWriter
from ddtrace.llmobs._writer import should_use_agentless
from ddtrace.llmobs.utils import Documents
from ddtrace.llmobs.utils import ExportedLLMObsSpan
from ddtrace.llmobs.utils import Messages
from ddtrace.propagation.http import HTTPPropagator


log = get_logger(__name__)


SUPPORTED_LLMOBS_INTEGRATIONS = {
    "anthropic": "anthropic",
    "bedrock": "botocore",
    "openai": "openai",
    "langchain": "langchain",
    "google_generativeai": "google_generativeai",
    "vertexai": "vertexai",
    "langgraph": "langgraph",
    "crewai": "crewai",
    "openai_agents": "openai_agents",
}


class LLMObs(Service):
    _instance = None  # type: LLMObs
    enabled = False

    def __init__(self, tracer=None):
        super(LLMObs, self).__init__()
        self.tracer = tracer or ddtrace.tracer
        self._llmobs_context_provider = LLMObsContextProvider()
        agentless_enabled = config._llmobs_agentless_enabled if config._llmobs_agentless_enabled is not None else True
        self._llmobs_span_writer = LLMObsSpanWriter(
            interval=float(os.getenv("_DD_LLMOBS_WRITER_INTERVAL", 1.0)),
            timeout=float(os.getenv("_DD_LLMOBS_WRITER_TIMEOUT", 5.0)),
            is_agentless=agentless_enabled,
        )
        self._llmobs_eval_metric_writer = LLMObsEvalMetricWriter(
            interval=float(os.getenv("_DD_LLMOBS_WRITER_INTERVAL", 1.0)),
            timeout=float(os.getenv("_DD_LLMOBS_WRITER_TIMEOUT", 5.0)),
            is_agentless=agentless_enabled,
        )
        self._evaluator_runner = EvaluatorRunner(
            interval=float(os.getenv("_DD_LLMOBS_EVALUATOR_INTERVAL", 1.0)),
            llmobs_service=self,
        )

        forksafe.register(self._child_after_fork)

        self._link_tracker = LinkTracker()
        self._annotations = []
        self._annotation_context_lock = forksafe.RLock()

        self._tool_call_tracker = ToolCallTracker()

    def _on_span_start(self, span):
        if self.enabled and span.span_type == SpanTypes.LLM:
            self._activate_llmobs_span(span)
            telemetry.record_span_started()
            self._do_annotations(span)

    def _on_span_finish(self, span):
        if self.enabled and span.span_type == SpanTypes.LLM:
            self._submit_llmobs_span(span)
            telemetry.record_span_created(span)

    def _submit_llmobs_span(self, span: Span) -> None:
        """Generate and submit an LLMObs span event to be sent to LLMObs."""
        span_event = None
        try:
            span_event = self._llmobs_span_event(span)
            self._llmobs_span_writer.enqueue(span_event)
        except (KeyError, TypeError):
            log.error(
                "Error generating LLMObs span event for span %s, likely due to malformed span", span, exc_info=True
            )
        finally:
            if not span_event or not span._get_ctx_item(SPAN_KIND) == "llm" or _is_evaluation_span(span):
                return
            if self._evaluator_runner:
                self._evaluator_runner.enqueue(span_event, span)

    @classmethod
    def _llmobs_span_event(cls, span: Span) -> Dict[str, Any]:
        """Span event object structure."""
        span_kind = span._get_ctx_item(SPAN_KIND)
        if not span_kind:
            raise KeyError("Span kind not found in span context")
        meta: Dict[str, Any] = {"span.kind": span_kind, "input": {}, "output": {}}
        if span_kind in ("llm", "embedding") and span._get_ctx_item(MODEL_NAME) is not None:
            meta["model_name"] = span._get_ctx_item(MODEL_NAME)
            meta["model_provider"] = (span._get_ctx_item(MODEL_PROVIDER) or "custom").lower()
        metadata = span._get_ctx_item(METADATA) or {}
        if span_kind == "agent" and span._get_ctx_item(AGENT_MANIFEST) is not None:
            metadata["agent_manifest"] = span._get_ctx_item(AGENT_MANIFEST)
        meta["metadata"] = metadata
        if span_kind == "llm" and span._get_ctx_item(INPUT_MESSAGES) is not None:
            meta["input"]["messages"] = span._get_ctx_item(INPUT_MESSAGES)
        if span._get_ctx_item(INPUT_VALUE) is not None:
            meta["input"]["value"] = safe_json(span._get_ctx_item(INPUT_VALUE), ensure_ascii=False)
        if span_kind == "llm" and span._get_ctx_item(OUTPUT_MESSAGES) is not None:
            meta["output"]["messages"] = span._get_ctx_item(OUTPUT_MESSAGES)
        if span_kind == "embedding" and span._get_ctx_item(INPUT_DOCUMENTS) is not None:
            meta["input"]["documents"] = span._get_ctx_item(INPUT_DOCUMENTS)
        if span._get_ctx_item(OUTPUT_VALUE) is not None:
            meta["output"]["value"] = safe_json(span._get_ctx_item(OUTPUT_VALUE), ensure_ascii=False)
        if span_kind == "retrieval" and span._get_ctx_item(OUTPUT_DOCUMENTS) is not None:
            meta["output"]["documents"] = span._get_ctx_item(OUTPUT_DOCUMENTS)
        if span._get_ctx_item(INPUT_PROMPT) is not None:
            prompt_json_str = span._get_ctx_item(INPUT_PROMPT)
            if span_kind != "llm":
                log.warning(
                    "Dropping prompt on non-LLM span kind, annotating prompts is only supported for LLM span kinds."
                )
            else:
                meta["input"]["prompt"] = prompt_json_str
        if span.error:
            meta.update(
                {
                    ERROR_MSG: span.get_tag(ERROR_MSG),
                    ERROR_STACK: span.get_tag(ERROR_STACK),
                    ERROR_TYPE: span.get_tag(ERROR_TYPE),
                }
            )
        if not meta["input"]:
            meta.pop("input")
        if not meta["output"]:
            meta.pop("output")
        metrics = span._get_ctx_item(METRICS) or {}
        ml_app = _get_ml_app(span)

        span._set_ctx_item(ML_APP, ml_app)
        parent_id = span._get_ctx_item(PARENT_ID_KEY) or ROOT_PARENT_ID

        llmobs_span_event = {
            "trace_id": format_trace_id(span.trace_id),
            "span_id": str(span.span_id),
            "parent_id": parent_id,
            "name": _get_span_name(span),
            "start_ns": span.start_ns,
            "duration": span.duration_ns,
            "status": "error" if span.error else "ok",
            "meta": meta,
            "metrics": metrics,
            "_dd": {"span_id": str(span.span_id), "trace_id": format_trace_id(span.trace_id)},
        }
        session_id = _get_session_id(span)
        if session_id is not None:
            span._set_ctx_item(SESSION_ID, session_id)
            llmobs_span_event["session_id"] = session_id

        llmobs_span_event["tags"] = cls._llmobs_tags(span, ml_app, session_id)

        span_links = span._get_ctx_item(SPAN_LINKS)
        if isinstance(span_links, list) and span_links:
            llmobs_span_event["span_links"] = span_links

        return llmobs_span_event

    @staticmethod
    def _llmobs_tags(span: Span, ml_app: str, session_id: Optional[str] = None) -> List[str]:
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
        if span._get_ctx_item(INTEGRATION):
            tags["integration"] = span._get_ctx_item(INTEGRATION)
        if _is_evaluation_span(span):
            tags[constants.RUNNER_IS_INTEGRATION_SPAN_TAG] = "ragas"
        existing_tags = span._get_ctx_item(TAGS)
        if existing_tags is not None:
            tags.update(existing_tags)
        return ["{}:{}".format(k, v) for k, v in tags.items()]

    def _do_annotations(self, span: Span) -> None:
        # get the current span context
        # only do the annotations if it matches the context
        if span.span_type != SpanTypes.LLM:  # do this check to avoid the warning log in `annotate`
            return
        current_context = self._instance.tracer.current_trace_context()
        current_context_id = current_context.get_baggage_item(ANNOTATIONS_CONTEXT_ID)
        with self._annotation_context_lock:
            for _, context_id, annotation_kwargs in self._instance._annotations:
                if current_context_id == context_id:
                    self.annotate(span, **annotation_kwargs)

    def _child_after_fork(self) -> None:
        self._llmobs_span_writer = self._llmobs_span_writer.recreate()
        self._llmobs_eval_metric_writer = self._llmobs_eval_metric_writer.recreate()
        self._evaluator_runner = self._evaluator_runner.recreate()
        if self.enabled:
            self._start_service()

    def _start_service(self) -> None:
        try:
            self._llmobs_span_writer.start()
            self._llmobs_eval_metric_writer.start()
        except ServiceStatusError:
            log.debug("Error starting LLMObs writers")

        try:
            self._evaluator_runner.start()
        except ServiceStatusError:
            log.debug("Error starting evaluator runner")

    def _stop_service(self) -> None:
        try:
            self._evaluator_runner.stop()
            # flush remaining evaluation spans & evaluations
            self._instance._llmobs_span_writer.periodic()
            self._instance._llmobs_eval_metric_writer.periodic()
        except ServiceStatusError:
            log.debug("Error stopping evaluator runner")

        try:
            self._llmobs_span_writer.stop()
            self._llmobs_eval_metric_writer.stop()
        except ServiceStatusError:
            log.debug("Error stopping LLMObs writers")

        # Remove listener hooks for span events
        core.reset_listeners("trace.span_start", self._on_span_start)
        core.reset_listeners("trace.span_finish", self._on_span_finish)
        core.reset_listeners("http.span_inject", self._inject_llmobs_context)
        core.reset_listeners("http.activate_distributed_headers", self._activate_llmobs_distributed_context)
        core.reset_listeners("threading.submit", self._current_trace_context)
        core.reset_listeners("threading.execution", self._llmobs_context_provider.activate)

        core.reset_listeners(DISPATCH_ON_LLM_TOOL_CHOICE, self._tool_call_tracker.on_llm_tool_choice)
        core.reset_listeners(DISPATCH_ON_TOOL_CALL, self._tool_call_tracker.on_tool_call)
        core.reset_listeners(DISPATCH_ON_TOOL_CALL_OUTPUT_USED, self._tool_call_tracker.on_tool_call_output_used)

        forksafe.unregister(self._child_after_fork)

    @classmethod
    def enable(
        cls,
        ml_app: Optional[str] = None,
        integrations_enabled: bool = True,
        agentless_enabled: Optional[bool] = None,
        site: Optional[str] = None,
        api_key: Optional[str] = None,
        env: Optional[str] = None,
        service: Optional[str] = None,
        _tracer: Optional[Tracer] = None,
        _auto: bool = False,
    ) -> None:
        """
        Enable LLM Observability tracing.

        :param str ml_app: The name of your ml application.
        :param bool integrations_enabled: Set to `true` to enable LLM integrations.
        :param bool agentless_enabled: Set to `true` to disable sending data that requires a Datadog Agent.
        :param str site: Your datadog site.
        :param str api_key: Your datadog api key.
        :param str env: Your environment name.
        :param str service: Your service name.
        """
        if cls.enabled:
            log.debug("%s already enabled", cls.__name__)
            return

        if os.getenv("DD_LLMOBS_ENABLED") and not asbool(os.getenv("DD_LLMOBS_ENABLED")):
            log.debug("LLMObs.enable() called when DD_LLMOBS_ENABLED is set to false or 0, not starting LLMObs service")
            return
        # grab required values for LLMObs
        config._dd_site = site or config._dd_site
        config._dd_api_key = api_key or config._dd_api_key
        config.env = env or config.env
        config.service = service or config.service
        config._llmobs_ml_app = ml_app or config._llmobs_ml_app

        error = None
        start_ns = time.time_ns()
        try:
            # validate required values for LLMObs
            if not config._llmobs_ml_app:
                error = "missing_ml_app"
                raise ValueError(
                    "DD_LLMOBS_ML_APP is required for sending LLMObs data. "
                    "Ensure this configuration is set before running your application."
                )

            config._llmobs_agentless_enabled = should_use_agentless(
                user_defined_agentless_enabled=agentless_enabled
                if agentless_enabled is not None
                else config._llmobs_agentless_enabled
            )

            if config._llmobs_agentless_enabled:
                # validate required values for agentless LLMObs
                if not config._dd_api_key:
                    error = "missing_api_key"
                    raise ValueError(
                        "DD_API_KEY is required for sending LLMObs data when agentless mode is enabled. "
                        "Ensure this configuration is set before running your application."
                    )
                if not config._dd_site:
                    error = "missing_site"
                    raise ValueError(
                        "DD_SITE is required for sending LLMObs data when agentless mode is enabled. "
                        "Ensure this configuration is set before running your application."
                    )
                if not os.getenv("DD_REMOTE_CONFIG_ENABLED"):
                    config._remote_config_enabled = False
                    log.debug("Remote configuration disabled because DD_LLMOBS_AGENTLESS_ENABLED is set to true.")
                    remoteconfig_poller.disable()

                # Since the API key can be set programmatically and TelemetryWriter is already initialized by now,
                # we need to force telemetry to use agentless configuration
                telemetry_writer.enable_agentless_client(True)

            if integrations_enabled:
                cls._patch_integrations()

            # override the default _instance with a new tracer
            cls._instance = cls(tracer=_tracer)
            cls.enabled = True
            cls._instance.start()

            # Register hooks for span events
            core.on("trace.span_start", cls._instance._on_span_start)
            core.on("trace.span_finish", cls._instance._on_span_finish)
            core.on("http.span_inject", cls._inject_llmobs_context)
            core.on("http.activate_distributed_headers", cls._activate_llmobs_distributed_context)
            core.on("threading.submit", cls._instance._current_trace_context, "llmobs_ctx")
            core.on("threading.execution", cls._instance._llmobs_context_provider.activate)

            core.on(DISPATCH_ON_LLM_TOOL_CHOICE, cls._instance._tool_call_tracker.on_llm_tool_choice)
            core.on(DISPATCH_ON_TOOL_CALL, cls._instance._tool_call_tracker.on_tool_call)
            core.on(DISPATCH_ON_TOOL_CALL_OUTPUT_USED, cls._instance._tool_call_tracker.on_tool_call_output_used)

            atexit.register(cls.disable)
            telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.LLMOBS, True)

            log.debug("%s enabled", cls.__name__)
        finally:
            telemetry.record_llmobs_enabled(error, config._llmobs_agentless_enabled, config._dd_site, start_ns, _auto)

    @classmethod
    def _integration_is_enabled(cls, integration: str) -> bool:
        if integration not in SUPPORTED_LLMOBS_INTEGRATIONS:
            return False
        return SUPPORTED_LLMOBS_INTEGRATIONS[integration] in ddtrace._monkey._get_patched_modules()

    @classmethod
    def disable(cls) -> None:
        if not cls.enabled:
            log.debug("%s not enabled", cls.__name__)
            return
        log.debug("Disabling %s", cls.__name__)
        atexit.unregister(cls.disable)

        cls._instance.stop()
        cls.enabled = False
        telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.LLMOBS, False)

        log.debug("%s disabled", cls.__name__)

    def _record_object(self, span, obj, input_or_output):
        if obj is None:
            return
        span_links = []
        for span_link in self._link_tracker.get_span_links_from_object(obj):
            try:
                if span_link["attributes"]["from"] == "input" and input_or_output == "output":
                    continue
            except KeyError:
                log.debug("failed to read span link: ", span_link)
                continue
            span_links.append(
                {
                    "trace_id": span_link["trace_id"],
                    "span_id": span_link["span_id"],
                    "attributes": {
                        "from": span_link["attributes"]["from"],
                        "to": input_or_output,
                    },
                }
            )
        self._tag_span_links(span, span_links)
        self._link_tracker.add_span_links_to_object(
            obj,
            [
                {
                    "trace_id": self.export_span(span)["trace_id"],
                    "span_id": self.export_span(span)["span_id"],
                    "attributes": {
                        "from": input_or_output,
                    },
                }
            ],
        )

    def _tag_span_links(self, span, span_links):
        if not span_links:
            return
        span_links = [
            span_link
            for span_link in span_links
            if span_link["span_id"] != LLMObs.export_span(span)["span_id"]
            and span_link["trace_id"] == LLMObs.export_span(span)["trace_id"]
        ]
        current_span_links = span._get_ctx_item(SPAN_LINKS)
        if current_span_links:
            span_links = current_span_links + span_links
        span._set_ctx_item(SPAN_LINKS, span_links)

    @classmethod
    def annotation_context(
        cls, tags: Optional[Dict[str, Any]] = None, prompt: Optional[dict] = None, name: Optional[str] = None
    ) -> AnnotationContext:
        """
        Sets specified attributes on all LLMObs spans created while the returned AnnotationContext is active.
        Annotations are applied in the order in which annotation contexts are entered.

        :param tags: Dictionary of JSON serializable key-value tag pairs to set or update on the LLMObs span
                     regarding the span's context.
        :param prompt: A dictionary that represents the prompt used for an LLM call in the following form:
                        `{"template": "...", "id": "...", "version": "...", "variables": {"variable_1": "...", ...}}`.
                        Can also be set using the `ddtrace.llmobs.utils.Prompt` constructor class.
                        - This argument is only applicable to LLM spans.
                        - The dictionary may contain two optional keys relevant to RAG applications:
                            `rag_context_variables` - a list of variable key names that contain ground
                                                        truth context information
                            `rag_query_variables` - a list of variable key names that contains query
                                                        information for an LLM call
        :param name: Set to override the span name for any spans annotated within the returned context.
        """
        # id to track an annotation for registering / de-registering
        annotation_id = rand64bits()

        def get_annotations_context_id():
            current_ctx = cls._instance.tracer.current_trace_context()
            # default the context id to the annotation id
            ctx_id = annotation_id
            if current_ctx is None:
                current_ctx = Context(is_remote=False)
                current_ctx.set_baggage_item(ANNOTATIONS_CONTEXT_ID, ctx_id)
                cls._instance.tracer.context_provider.activate(current_ctx)
            elif not current_ctx.get_baggage_item(ANNOTATIONS_CONTEXT_ID):
                current_ctx.set_baggage_item(ANNOTATIONS_CONTEXT_ID, ctx_id)
            else:
                ctx_id = current_ctx.get_baggage_item(ANNOTATIONS_CONTEXT_ID)
            return ctx_id

        def register_annotation():
            with cls._instance._annotation_context_lock:
                ctx_id = get_annotations_context_id()
                cls._instance._annotations.append(
                    (annotation_id, ctx_id, {"tags": tags, "prompt": prompt, "_name": name})
                )

        def deregister_annotation():
            with cls._instance._annotation_context_lock:
                for i, (key, _, _) in enumerate(cls._instance._annotations):
                    if key == annotation_id:
                        cls._instance._annotations.pop(i)
                        return
                else:
                    log.debug("Failed to pop annotation context")

        return AnnotationContext(register_annotation, deregister_annotation)

    @classmethod
    def flush(cls) -> None:
        """
        Flushes any remaining spans and evaluation metrics to the LLMObs backend.
        """
        if cls.enabled is False:
            log.warning("flushing when LLMObs is disabled. No spans or evaluation metrics will be sent.")
            return

        error = None
        try:
            cls._instance._evaluator_runner.periodic()
        except Exception:
            error = "evaluator_flush_error"
            log.warning("Failed to run evaluator runner.", exc_info=True)

        try:
            cls._instance._llmobs_span_writer.periodic()
            cls._instance._llmobs_eval_metric_writer.periodic()
        except Exception:
            error = "writer_flush_error"
            log.warning("Failed to flush LLMObs spans and evaluation metrics.", exc_info=True)

        telemetry.record_user_flush(error)

    @staticmethod
    def _patch_integrations() -> None:
        """
        Patch LLM integrations. Ensure that we do not ignore DD_TRACE_<MODULE>_ENABLED or DD_PATCH_MODULES settings.
        """
        integrations_to_patch: Dict[str, Union[List[str], bool]] = {
            integration: ["bedrock-runtime"] if integration == "botocore" else True
            for integration in SUPPORTED_LLMOBS_INTEGRATIONS.values()
        }
        for module, _ in integrations_to_patch.items():
            env_var = "DD_TRACE_%s_ENABLED" % module.upper()
            if env_var in os.environ:
                integrations_to_patch[module] = asbool(os.environ[env_var])
        dd_patch_modules = os.getenv("DD_PATCH_MODULES")
        dd_patch_modules_to_str = parse_tags_str(dd_patch_modules)
        integrations_to_patch.update(
            {k: asbool(v) for k, v in dd_patch_modules_to_str.items() if k in SUPPORTED_LLMOBS_INTEGRATIONS.values()}
        )
        patch(raise_errors=True, **integrations_to_patch)
        log.debug("Patched LLM integrations: %s", list(SUPPORTED_LLMOBS_INTEGRATIONS.values()))

    @classmethod
    def export_span(cls, span: Optional[Span] = None) -> Optional[ExportedLLMObsSpan]:
        """Returns a simple representation of a span to export its span and trace IDs.
        If no span is provided, the current active LLMObs-type span will be used.
        """
        if span is None:
            span = cls._instance._current_span()
            if span is None:
                telemetry.record_span_exported(span, "no_active_span")
                log.warning("No span provided and no active LLMObs-generated span found.")
                return None
        error = None
        try:
            if span.span_type != SpanTypes.LLM:
                error = "invalid_span"
                log.warning("Span must be an LLMObs-generated span.")
                return None
            return ExportedLLMObsSpan(span_id=str(span.span_id), trace_id=format_trace_id(span.trace_id))
        except (TypeError, AttributeError):
            error = "invalid_span"
            log.warning("Failed to export span. Span must be a valid Span object.")
            return None
        finally:
            telemetry.record_span_exported(span, error)

    def _current_span(self) -> Optional[Span]:
        """Returns the currently active LLMObs-generated span.
        Note that there may be an active span represented by a context object
        (i.e. a distributed trace) which will not be returned by this method.
        """
        active = self._llmobs_context_provider.active()
        return active if isinstance(active, Span) else None

    def _current_trace_context(self) -> Optional[Context]:
        """Returns the context for the current LLMObs trace."""
        active = self._llmobs_context_provider.active()
        if isinstance(active, Context):
            return active
        elif isinstance(active, Span):
            return active.context
        return None

    def _activate_llmobs_span(self, span: Span) -> None:
        """Propagate the llmobs parent span's ID as the new span's parent ID and activate the new span."""
        llmobs_parent = self._llmobs_context_provider.active()
        if llmobs_parent:
            span._set_ctx_item(PARENT_ID_KEY, str(llmobs_parent.span_id))
        else:
            span._set_ctx_item(PARENT_ID_KEY, ROOT_PARENT_ID)
        self._llmobs_context_provider.activate(span)

    def _start_span(
        self,
        operation_kind: str,
        name: Optional[str] = None,
        session_id: Optional[str] = None,
        model_name: Optional[str] = None,
        model_provider: Optional[str] = None,
        ml_app: Optional[str] = None,
        _decorator: bool = False,
    ) -> Span:
        if name is None:
            name = operation_kind
        span = self.tracer.trace(name, resource=operation_kind, span_type=SpanTypes.LLM)
        span._set_ctx_item(SPAN_KIND, operation_kind)
        if model_name is not None:
            span._set_ctx_item(MODEL_NAME, model_name)
        if model_provider is not None:
            span._set_ctx_item(MODEL_PROVIDER, model_provider)
        session_id = session_id if session_id is not None else _get_session_id(span)
        if session_id is not None:
            span._set_ctx_item(SESSION_ID, session_id)
        if ml_app is None:
            ml_app = _get_ml_app(span)
        span._set_ctx_items({DECORATOR: _decorator, SPAN_KIND: operation_kind, ML_APP: ml_app})
        return span

    @classmethod
    def llm(
        cls,
        model_name: Optional[str] = None,
        name: Optional[str] = None,
        model_provider: Optional[str] = None,
        session_id: Optional[str] = None,
        ml_app: Optional[str] = None,
        _decorator: bool = False,
    ) -> Span:
        """
        Trace an invocation call to an LLM where inputs and outputs are represented as text.

        :param str model_name: The name of the invoked LLM. If not provided, a default value of "custom" will be set.
        :param str name: The name of the traced operation. If not provided, a default value of "llm" will be set.
        :param str model_provider: The name of the invoked LLM provider (ex: openai, bedrock).
                                   If not provided, a default value of "custom" will be set.
        :param str session_id: The ID of the underlying user session. Required for tracking sessions.
        :param str ml_app: The name of the ML application that the agent is orchestrating. If not provided, the default
                           value will be set to the value of `DD_LLMOBS_ML_APP`.

        :returns: The Span object representing the traced operation.
        """
        if cls.enabled is False:
            log.warning(SPAN_START_WHILE_DISABLED_WARNING)
        if model_name is None:
            model_name = "custom"
        if model_provider is None:
            model_provider = "custom"
        return cls._instance._start_span(
            "llm",
            name,
            model_name=model_name,
            model_provider=model_provider,
            session_id=session_id,
            ml_app=ml_app,
            _decorator=_decorator,
        )

    @classmethod
    def tool(
        cls,
        name: Optional[str] = None,
        session_id: Optional[str] = None,
        ml_app: Optional[str] = None,
        _decorator: bool = False,
    ) -> Span:
        """
        Trace a call to an external interface or API.

        :param str name: The name of the traced operation. If not provided, a default value of "tool" will be set.
        :param str session_id: The ID of the underlying user session. Required for tracking sessions.
        :param str ml_app: The name of the ML application that the agent is orchestrating. If not provided, the default
                           value will be set to the value of `DD_LLMOBS_ML_APP`.

        :returns: The Span object representing the traced operation.
        """
        if cls.enabled is False:
            log.warning(SPAN_START_WHILE_DISABLED_WARNING)
        return cls._instance._start_span("tool", name=name, session_id=session_id, ml_app=ml_app, _decorator=_decorator)

    @classmethod
    def task(
        cls,
        name: Optional[str] = None,
        session_id: Optional[str] = None,
        ml_app: Optional[str] = None,
        _decorator: bool = False,
    ) -> Span:
        """
        Trace a standalone non-LLM operation which does not involve an external request.

        :param str name: The name of the traced operation. If not provided, a default value of "task" will be set.
        :param str session_id: The ID of the underlying user session. Required for tracking sessions.
        :param str ml_app: The name of the ML application that the agent is orchestrating. If not provided, the default
                           value will be set to the value of `DD_LLMOBS_ML_APP`.

        :returns: The Span object representing the traced operation.
        """
        if cls.enabled is False:
            log.warning(SPAN_START_WHILE_DISABLED_WARNING)
        return cls._instance._start_span("task", name=name, session_id=session_id, ml_app=ml_app, _decorator=_decorator)

    @classmethod
    def agent(
        cls,
        name: Optional[str] = None,
        session_id: Optional[str] = None,
        ml_app: Optional[str] = None,
        _decorator: bool = False,
    ) -> Span:
        """
        Trace a dynamic workflow in which an embedded language model (agent) decides what sequence of actions to take.

        :param str name: The name of the traced operation. If not provided, a default value of "agent" will be set.
        :param str session_id: The ID of the underlying user session. Required for tracking sessions.
        :param str ml_app: The name of the ML application that the agent is orchestrating. If not provided, the default
                           value will be set to the value of `DD_LLMOBS_ML_APP`.

        :returns: The Span object representing the traced operation.
        """
        if cls.enabled is False:
            log.warning(SPAN_START_WHILE_DISABLED_WARNING)
        return cls._instance._start_span(
            "agent", name=name, session_id=session_id, ml_app=ml_app, _decorator=_decorator
        )

    @classmethod
    def workflow(
        cls,
        name: Optional[str] = None,
        session_id: Optional[str] = None,
        ml_app: Optional[str] = None,
        _decorator: bool = False,
    ) -> Span:
        """
        Trace a predefined or static sequence of operations.

        :param str name: The name of the traced operation. If not provided, a default value of "workflow" will be set.
        :param str session_id: The ID of the underlying user session. Required for tracking sessions.
        :param str ml_app: The name of the ML application that the agent is orchestrating. If not provided, the default
                           value will be set to the value of `DD_LLMOBS_ML_APP`.

        :returns: The Span object representing the traced operation.
        """
        if cls.enabled is False:
            log.warning(SPAN_START_WHILE_DISABLED_WARNING)
        return cls._instance._start_span(
            "workflow", name=name, session_id=session_id, ml_app=ml_app, _decorator=_decorator
        )

    @classmethod
    def embedding(
        cls,
        model_name: Optional[str] = None,
        name: Optional[str] = None,
        model_provider: Optional[str] = None,
        session_id: Optional[str] = None,
        ml_app: Optional[str] = None,
        _decorator: bool = False,
    ) -> Span:
        """
        Trace a call to an embedding model or function to create an embedding.

        :param str model_name: The name of the invoked embedding model.
                               If not provided, a default value of "custom" will be set.
        :param str name: The name of the traced operation. If not provided, a default value of "embedding" will be set.
        :param str model_provider: The name of the invoked LLM provider (ex: openai, bedrock).
                                   If not provided, a default value of "custom" will be set.
        :param str session_id: The ID of the underlying user session. Required for tracking sessions.
        :param str ml_app: The name of the ML application that the agent is orchestrating. If not provided, the default
                           value will be set to the value of `DD_LLMOBS_ML_APP`.

        :returns: The Span object representing the traced operation.
        """
        if cls.enabled is False:
            log.warning(SPAN_START_WHILE_DISABLED_WARNING)
        if model_name is None:
            model_name = "custom"
        if model_provider is None:
            model_provider = "custom"
        return cls._instance._start_span(
            "embedding",
            name,
            model_name=model_name,
            model_provider=model_provider,
            session_id=session_id,
            ml_app=ml_app,
            _decorator=_decorator,
        )

    @classmethod
    def retrieval(
        cls,
        name: Optional[str] = None,
        session_id: Optional[str] = None,
        ml_app: Optional[str] = None,
        _decorator: bool = False,
    ) -> Span:
        """
        Trace a vector search operation involving a list of documents being returned from an external knowledge base.

        :param str name: The name of the traced operation. If not provided, a default value of "workflow" will be set.
        :param str session_id: The ID of the underlying user session. Required for tracking sessions.
        :param str ml_app: The name of the ML application that the agent is orchestrating. If not provided, the default
                           value will be set to the value of `DD_LLMOBS_ML_APP`.

        :returns: The Span object representing the traced operation.
        """
        if cls.enabled is False:
            log.warning(SPAN_START_WHILE_DISABLED_WARNING)
        return cls._instance._start_span(
            "retrieval", name=name, session_id=session_id, ml_app=ml_app, _decorator=_decorator
        )

    @classmethod
    def annotate(
        cls,
        span: Optional[Span] = None,
        prompt: Optional[dict] = None,
        input_data: Optional[Any] = None,
        output_data: Optional[Any] = None,
        metadata: Optional[Dict[str, Any]] = None,
        metrics: Optional[Dict[str, Any]] = None,
        tags: Optional[Dict[str, Any]] = None,
        _name: Optional[str] = None,
    ) -> None:
        """
        Sets metadata, inputs, outputs, tags, and metrics as provided for a given LLMObs span.
        Note that with the exception of tags, this method will override any existing values for the provided fields.

        :param Span span: Span to annotate. If no span is provided, the current active span will be used.
                          Must be an LLMObs-type span, i.e. generated by the LLMObs SDK.
        :param prompt: A dictionary that represents the prompt used for an LLM call in the following form:
                        `{"template": "...", "id": "...", "version": "...", "variables": {"variable_1": "...", ...}}`.
                        Can also be set using the `ddtrace.llmobs.utils.Prompt` constructor class.
                        - This argument is only applicable to LLM spans.
                        - The dictionary may contain two optional keys relevant to RAG applications:
                            `rag_context_variables` - a list of variable key names that contain ground
                                                        truth context information
                            `rag_query_variables` - a list of variable key names that contains query
                                                        information for an LLM call
        :param input_data: A single input string, dictionary, or a list of dictionaries based on the span kind:
                           - llm spans: accepts a string, or a dictionary of form {"content": "...", "role": "..."},
                                        or a list of dictionaries with the same signature.
                           - embedding spans: accepts a string, list of strings, or a dictionary of form
                                              {"text": "...", ...} or a list of dictionaries with the same signature.
                           - other: any JSON serializable type.
        :param output_data: A single output string, dictionary, or a list of dictionaries based on the span kind:
                           - llm spans: accepts a string, or a dictionary of form {"content": "...", "role": "..."},
                                        or a list of dictionaries with the same signature.
                           - retrieval spans: a dictionary containing any of the key value pairs
                                              {"name": str, "id": str, "text": str, "score": float},
                                              or a list of dictionaries with the same signature.
                           - other: any JSON serializable type.
        :param metadata: Dictionary of JSON serializable key-value metadata pairs relevant to the input/output operation
                         described by the LLMObs span.
        :param tags: Dictionary of JSON serializable key-value tag pairs to set or update on the LLMObs span
                     regarding the span's context.
        :param metrics: Dictionary of JSON serializable key-value metric pairs,
                        such as `{prompt,completion,total}_tokens`.
        """
        error = None
        try:
            if span is None:
                span = cls._instance._current_span()
                if span is None:
                    error = "invalid_span_no_active_spans"
                    log.warning("No span provided and no active LLMObs-generated span found.")
                    return
            if span.span_type != SpanTypes.LLM:
                error = "invalid_span_type"
                log.warning("Span must be an LLMObs-generated span.")
                return
            if span.finished:
                error = "invalid_finished_span"
                log.warning("Cannot annotate a finished span.")
                return
            if metadata is not None:
                if not isinstance(metadata, dict):
                    error = "invalid_metadata"
                    log.warning("metadata must be a dictionary")
                else:
                    cls._set_dict_attribute(span, METADATA, metadata)
            if metrics is not None:
                if not isinstance(metrics, dict):
                    error = "invalid_metrics"
                    log.warning("metrics must be a dictionary of string key - numeric value pairs.")
                else:
                    cls._set_dict_attribute(span, METRICS, metrics)
            if tags is not None:
                if not isinstance(tags, dict):
                    error = "invalid_tags"
                    log.warning("span tags must be a dictionary of string key - primitive value pairs.")
                else:
                    session_id = tags.get("session_id")
                    if session_id:
                        span._set_ctx_item(SESSION_ID, str(session_id))
                    cls._set_dict_attribute(span, TAGS, tags)
            span_kind = span._get_ctx_item(SPAN_KIND)
            if _name is not None:
                span.name = _name
            if prompt is not None:
                try:
                    validated_prompt = validate_prompt(prompt)
                    cls._set_dict_attribute(span, INPUT_PROMPT, validated_prompt)
                except TypeError:
                    error = "invalid_prompt"
                    log.warning("Failed to validate prompt with error: ", exc_info=True)
            if not span_kind:
                log.debug("Span kind not specified, skipping annotation for input/output data")
                return
            if input_data is not None or output_data is not None:
                if span_kind == "llm":
                    error = cls._tag_llm_io(span, input_messages=input_data, output_messages=output_data)
                elif span_kind == "embedding":
                    error = cls._tag_embedding_io(span, input_documents=input_data, output_text=output_data)
                elif span_kind == "retrieval":
                    error = cls._tag_retrieval_io(span, input_text=input_data, output_documents=output_data)
                else:
                    cls._tag_text_io(span, input_value=input_data, output_value=output_data)
        finally:
            telemetry.record_llmobs_annotate(span, error)

    @classmethod
    def _tag_llm_io(cls, span, input_messages=None, output_messages=None) -> Optional[str]:
        """Tags input/output messages for LLM-kind spans.
        Will be mapped to span's `meta.{input,output}.messages` fields.
        """
        if input_messages is not None:
            try:
                if not isinstance(input_messages, Messages):
                    input_messages = Messages(input_messages)
                if input_messages.messages:
                    span._set_ctx_item(INPUT_MESSAGES, input_messages.messages)
            except TypeError:
                log.warning("Failed to parse input messages.", exc_info=True)
                return "invalid_io_messages"
        if output_messages is None:
            return None
        try:
            if not isinstance(output_messages, Messages):
                output_messages = Messages(output_messages)
            if not output_messages.messages:
                return None
            span._set_ctx_item(OUTPUT_MESSAGES, output_messages.messages)
        except TypeError:
            log.warning("Failed to parse output messages.", exc_info=True)
            return "invalid_io_messages"
        return None

    @classmethod
    def _tag_embedding_io(cls, span, input_documents=None, output_text=None) -> Optional[str]:
        """Tags input documents and output text for embedding-kind spans.
        Will be mapped to span's `meta.{input,output}.text` fields.
        """
        if input_documents is not None:
            try:
                if not isinstance(input_documents, Documents):
                    input_documents = Documents(input_documents)
                if input_documents.documents:
                    span._set_ctx_item(INPUT_DOCUMENTS, input_documents.documents)
            except TypeError:
                log.warning("Failed to parse input documents.", exc_info=True)
                return "invalid_embedding_io"
        if output_text is None:
            return None
        span._set_ctx_item(OUTPUT_VALUE, str(output_text))
        return None

    @classmethod
    def _tag_retrieval_io(cls, span, input_text=None, output_documents=None) -> Optional[str]:
        """Tags input text and output documents for retrieval-kind spans.
        Will be mapped to span's `meta.{input,output}.text` fields.
        """
        if input_text is not None:
            span._set_ctx_item(INPUT_VALUE, safe_json(input_text))
        if output_documents is None:
            return None
        try:
            if not isinstance(output_documents, Documents):
                output_documents = Documents(output_documents)
            if not output_documents.documents:
                return None
            span._set_ctx_item(OUTPUT_DOCUMENTS, output_documents.documents)
        except TypeError:
            log.warning("Failed to parse output documents.", exc_info=True)
            return "invalid_retrieval_io"
        return None

    @classmethod
    def _tag_text_io(cls, span, input_value=None, output_value=None):
        """Tags input/output values for non-LLM kind spans.
        Will be mapped to span's `meta.{input,output}.values` fields.
        """
        if input_value is not None:
            span._set_ctx_item(INPUT_VALUE, safe_json(input_value))
        if output_value is not None:
            span._set_ctx_item(OUTPUT_VALUE, safe_json(output_value))

    @staticmethod
    def _set_dict_attribute(span: Span, key, value: Dict[str, Any]) -> None:
        """Sets a given LLM Obs span attribute with a dictionary key/values.
        If the attribute is already set on the span, the new dict with be merged with the existing
        dict.
        """
        existing_value = span._get_ctx_item(key) or {}
        existing_value.update(value)
        span._set_ctx_item(key, existing_value)

    @classmethod
    def submit_evaluation_for(
        cls,
        label: str,
        metric_type: str,
        value: Union[str, int, float],
        span: Optional[dict] = None,
        span_with_tag_value: Optional[Dict[str, str]] = None,
        tags: Optional[Dict[str, str]] = None,
        ml_app: Optional[str] = None,
        timestamp_ms: Optional[int] = None,
        metadata: Optional[Dict[str, object]] = None,
    ) -> None:
        """
        Submits a custom evaluation metric for a given span.

        :param str label: The name of the evaluation metric.
        :param str metric_type: The type of the evaluation metric. One of "categorical", "score".
        :param value: The value of the evaluation metric.
                      Must be a string (categorical), integer (score), or float (score).
        :param dict span: A dictionary of shape {'span_id': str, 'trace_id': str} uniquely identifying
                            the span associated with this evaluation.
        :param dict span_with_tag_value: A dictionary with the format {'tag_key': str, 'tag_value': str}
                            uniquely identifying the span associated with this evaluation.
        :param tags: A dictionary of string key-value pairs to tag the evaluation metric with.
        :param str ml_app: The name of the ML application
        :param int timestamp_ms: The unix timestamp in milliseconds when the evaluation metric result was generated.
                                    If not set, the current time will be used.
        :param dict metadata: A JSON serializable dictionary of key-value metadata pairs relevant to the
                                evaluation metric.
        """
        if cls.enabled is False:
            log.debug(
                "LLMObs.submit_evaluation_for() called when LLMObs is not enabled. ",
                "Evaluation metric data will not be sent.",
            )
            return

        error = None
        join_on = {}
        try:
            has_exactly_one_joining_key = (span is not None) ^ (span_with_tag_value is not None)

            if not has_exactly_one_joining_key:
                error = "provided_both_span_and_tag_joining_key"
                raise ValueError(
                    "Exactly one of `span` or `span_with_tag_value` must be specified to submit an evaluation metric."
                )

            if span is not None:
                if (
                    not isinstance(span, dict)
                    or not isinstance(span.get("span_id"), str)
                    or not isinstance(span.get("trace_id"), str)
                ):
                    error = "invalid_span"
                    raise TypeError(
                        "`span` must be a dictionary containing both span_id and trace_id keys. "
                        "LLMObs.export_span() can be used to generate this dictionary from a given span."
                    )
                join_on["span"] = span
            elif span_with_tag_value is not None:
                if (
                    not isinstance(span_with_tag_value, dict)
                    or not isinstance(span_with_tag_value.get("tag_key"), str)
                    or not isinstance(span_with_tag_value.get("tag_value"), str)
                ):
                    error = "invalid_joining_key"
                    raise TypeError(
                        "`span_with_tag_value` must be a dict with keys 'tag_key' and 'tag_value' "
                        "containing string values"
                    )
                join_on["tag"] = {
                    "key": span_with_tag_value.get("tag_key"),
                    "value": span_with_tag_value.get("tag_value"),
                }

            timestamp_ms = timestamp_ms if timestamp_ms else int(time.time() * 1000)

            if not isinstance(timestamp_ms, int) or timestamp_ms < 0:
                error = "invalid_timestamp"
                raise ValueError("timestamp_ms must be a non-negative integer. Evaluation metric data will not be sent")

            if not label:
                error = "invalid_metric_label"
                raise ValueError("label must be the specified name of the evaluation metric.")

            metric_type = metric_type.lower()
            if metric_type not in ("categorical", "score"):
                error = "invalid_metric_type"
                raise ValueError("metric_type must be one of 'categorical' or 'score'.")

            if metric_type == "categorical" and not isinstance(value, str):
                error = "invalid_metric_value"
                raise TypeError("value must be a string for a categorical metric.")
            if metric_type == "score" and not isinstance(value, (int, float)):
                error = "invalid_metric_value"
                raise TypeError("value must be an integer or float for a score metric.")

            if tags is not None and not isinstance(tags, dict):
                log.warning("tags must be a dictionary of string key-value pairs.")
                tags = {}

            evaluation_tags = {
                "ddtrace.version": ddtrace.__version__,
                "ml_app": ml_app,
            }

            if tags:
                for k, v in tags.items():
                    try:
                        evaluation_tags[ensure_text(k)] = ensure_text(v)
                    except TypeError:
                        error = "invalid_tags"
                        log.warning("Failed to parse tags. Tags for evaluation metrics must be strings.")

            ml_app = ml_app if ml_app else config._llmobs_ml_app
            if not ml_app:
                error = "missing_ml_app"
                log.warning(
                    "ML App name is required for sending evaluation metrics. Evaluation metric data will not be sent. "
                    "Ensure this configuration is set before running your application."
                )
                return

            evaluation_metric = {
                "join_on": join_on,
                "label": str(label),
                "metric_type": metric_type,
                "timestamp_ms": timestamp_ms,
                "{}_value".format(metric_type): value,
                "ml_app": ml_app,
                "tags": ["{}:{}".format(k, v) for k, v in evaluation_tags.items()],
            }

            if metadata:
                if not isinstance(metadata, dict):
                    error = "invalid_metadata"
                    log.warning("metadata must be json serializable dictionary.")
                else:
                    metadata = safe_json(metadata)
                    if metadata and isinstance(metadata, str):
                        evaluation_metric["metadata"] = json.loads(metadata)

            cls._instance._llmobs_eval_metric_writer.enqueue(evaluation_metric)
        finally:
            telemetry.record_llmobs_submit_evaluation(join_on, metric_type, error)

    @classmethod
    def submit_evaluation(
        cls,
        span_context: Dict[str, str],
        label: str,
        metric_type: str,
        value: Union[str, int, float],
        tags: Optional[Dict[str, str]] = None,
        ml_app: Optional[str] = None,
        timestamp_ms: Optional[int] = None,
        metadata: Optional[Dict[str, object]] = None,
    ) -> None:
        """
        Submits a custom evaluation metric for a given span ID and trace ID.

        :param span_context: A dictionary containing the span_id and trace_id of interest.
        :param str label: The name of the evaluation metric.
        :param str metric_type: The type of the evaluation metric. One of "categorical", "score".
        :param value: The value of the evaluation metric.
                      Must be a string (categorical), integer (score), or float (score).
        :param tags: A dictionary of string key-value pairs to tag the evaluation metric with.
        :param str ml_app: The name of the ML application
        :param int timestamp_ms: The timestamp in milliseconds when the evaluation metric result was generated.
        :param dict metadata: A JSON serializable dictionary of key-value metadata pairs relevant to the
                                evaluation metric.
        """
        if cls.enabled is False:
            log.debug(
                "LLMObs.submit_evaluation() called when LLMObs is not enabled. Evaluation metric data will not be sent."
            )
            return
        error = None
        try:
            if not isinstance(span_context, dict):
                error = "invalid_span"
                log.warning(
                    "span_context must be a dictionary containing both span_id and trace_id keys. "
                    "LLMObs.export_span() can be used to generate this dictionary from a given span."
                )
                return

            ml_app = ml_app if ml_app else config._llmobs_ml_app
            if not ml_app:
                error = "missing_ml_app"
                log.warning(
                    "ML App name is required for sending evaluation metrics. Evaluation metric data will not be sent. "
                    "Ensure this configuration is set before running your application."
                )
                return

            timestamp_ms = timestamp_ms if timestamp_ms else int(time.time() * 1000)

            if not isinstance(timestamp_ms, int) or timestamp_ms < 0:
                error = "invalid_timestamp"
                log.warning("timestamp_ms must be a non-negative integer. Evaluation metric data will not be sent")
                return

            span_id = span_context.get("span_id")
            trace_id = span_context.get("trace_id")
            if not (span_id and trace_id):
                error = "invalid_span"
                log.warning(
                    "span_id and trace_id must both be specified for the given evaluation metric to be submitted."
                )
                return
            if not label:
                error = "invalid_metric_label"
                log.warning("label must be the specified name of the evaluation metric.")
                return

            if not metric_type or metric_type.lower() not in ("categorical", "numerical", "score"):
                error = "invalid_metric_type"
                log.warning("metric_type must be one of 'categorical' or 'score'.")
                return

            metric_type = metric_type.lower()
            if metric_type == "numerical":
                error = "invalid_metric_type"
                log.warning(
                    "The evaluation metric type 'numerical' is unsupported. Use 'score' instead. "
                    "Converting `numerical` metric to `score` type."
                )
                metric_type = "score"

            if metric_type == "categorical" and not isinstance(value, str):
                error = "invalid_metric_value"
                log.warning("value must be a string for a categorical metric.")
                return
            if metric_type == "score" and not isinstance(value, (int, float)):
                error = "invalid_metric_value"
                log.warning("value must be an integer or float for a score metric.")
                return
            if tags is not None and not isinstance(tags, dict):
                error = "invalid_tags"
                log.warning("tags must be a dictionary of string key-value pairs.")
                return

            # initialize tags with default values that will be overridden by user-provided tags
            evaluation_tags = {
                "ddtrace.version": ddtrace.__version__,
                "ml_app": ml_app,
            }

            if tags:
                for k, v in tags.items():
                    try:
                        evaluation_tags[ensure_text(k)] = ensure_text(v)
                    except TypeError:
                        error = "invalid_tags"
                        log.warning("Failed to parse tags. Tags for evaluation metrics must be strings.")

            evaluation_metric = {
                "join_on": {"span": {"span_id": span_id, "trace_id": trace_id}},
                "label": str(label),
                "metric_type": metric_type.lower(),
                "timestamp_ms": timestamp_ms,
                "{}_value".format(metric_type): value,
                "ml_app": ml_app,
                "tags": ["{}:{}".format(k, v) for k, v in evaluation_tags.items()],
            }

            if metadata:
                if not isinstance(metadata, dict):
                    error = "invalid_metadata"
                    log.warning("metadata must be json serializable dictionary.")
                else:
                    metadata = safe_json(metadata)
                    if metadata and isinstance(metadata, str):
                        evaluation_metric["metadata"] = json.loads(metadata)

            cls._instance._llmobs_eval_metric_writer.enqueue(evaluation_metric)
        finally:
            telemetry.record_llmobs_submit_evaluation({"span": span_context}, metric_type, error)

    @classmethod
    def _inject_llmobs_context(cls, span_context: Context, request_headers: Dict[str, str]) -> None:
        if cls.enabled is False:
            return
        active_context = cls._instance._current_trace_context()
        if active_context is None:
            parent_id = ROOT_PARENT_ID
        else:
            parent_id = str(active_context.span_id)
        span_context._meta[PROPAGATED_PARENT_ID_KEY] = parent_id

    @classmethod
    def inject_distributed_headers(cls, request_headers: Dict[str, str], span: Optional[Span] = None) -> Dict[str, str]:
        """Injects the span's distributed context into the given request headers."""
        if cls.enabled is False:
            log.warning(
                "LLMObs.inject_distributed_headers() called when LLMObs is not enabled. "
                "Distributed context will not be injected."
            )
            return request_headers
        error = None
        try:
            if not isinstance(request_headers, dict):
                error = "invalid_request_headers"
                log.warning("request_headers must be a dictionary of string key-value pairs.")
                return request_headers
            if span is None:
                span = cls._instance.tracer.current_span()
            if span is None:
                error = "no_active_span"
                log.warning("No span provided and no currently active span found.")
                return request_headers
            if not isinstance(span, Span):
                error = "invalid_span"
                log.warning("span must be a valid Span object. Distributed context will not be injected.")
                return request_headers
            HTTPPropagator.inject(span.context, request_headers)
            return request_headers
        finally:
            telemetry.record_inject_distributed_headers(error)

    @classmethod
    def _activate_llmobs_distributed_context(cls, request_headers: Dict[str, str], context: Context) -> Optional[str]:
        if cls.enabled is False:
            return None
        if not context.trace_id or not context.span_id:
            log.warning("Failed to extract trace/span ID from request headers.")
            return "missing_context"
        _parent_id = context._meta.get(PROPAGATED_PARENT_ID_KEY)
        if _parent_id is None:
            log.warning("Failed to extract LLMObs parent ID from request headers.")
            return "missing_parent_id"
        try:
            parent_id = int(_parent_id)
        except ValueError:
            log.warning("Failed to parse LLMObs parent ID from request headers.")
            return "invalid_parent_id"
        llmobs_context = Context(trace_id=context.trace_id, span_id=parent_id)
        cls._instance._llmobs_context_provider.activate(llmobs_context)
        return None

    @classmethod
    def activate_distributed_headers(cls, request_headers: Dict[str, str]) -> None:
        """
        Activates distributed tracing headers for the current request.

        :param request_headers: A dictionary containing the headers for the current request.
        """
        if cls.enabled is False:
            log.warning(
                "LLMObs.activate_distributed_headers() called when LLMObs is not enabled. "
                "Distributed context will not be activated."
            )
            return
        context = HTTPPropagator.extract(request_headers)
        cls._instance.tracer.context_provider.activate(context)
        error = cls._instance._activate_llmobs_distributed_context(request_headers, context)
        telemetry.record_activate_distributed_headers(error)


# initialize the default llmobs instance
LLMObs._instance = LLMObs()
