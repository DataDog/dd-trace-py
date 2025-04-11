from contextlib import contextmanager
import functools
from inspect import iscoroutinefunction
from itertools import chain
import logging
import os
from os import environ
from os import getpid
import sys
from threading import RLock
from typing import TYPE_CHECKING
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import TypeVar
from typing import Union

from ddtrace._hooks import Hooks
from ddtrace._trace.context import Context
from ddtrace._trace.processor import SpanAggregator
from ddtrace._trace.processor import SpanProcessor
from ddtrace._trace.processor import TopLevelSpanProcessor
from ddtrace._trace.processor import TraceProcessor
from ddtrace._trace.processor import TraceSamplingProcessor
from ddtrace._trace.processor import TraceTagsProcessor
from ddtrace._trace.provider import BaseContextProvider
from ddtrace._trace.provider import DefaultContextProvider
from ddtrace._trace.sampler import DatadogSampler
from ddtrace._trace.span import Span
from ddtrace.appsec._constants import APPSEC
from ddtrace.constants import _HOSTNAME_KEY
from ddtrace.constants import ENV_KEY
from ddtrace.constants import PID
from ddtrace.constants import VERSION_KEY
from ddtrace.internal import atexit
from ddtrace.internal import compat
from ddtrace.internal import debug
from ddtrace.internal import forksafe
from ddtrace.internal import hostname
from ddtrace.internal.atexit import register_on_exit_signal
from ddtrace.internal.constants import SAMPLING_DECISION_TRACE_TAG_KEY
from ddtrace.internal.constants import SPAN_API_DATADOG
from ddtrace.internal.core import dispatch
from ddtrace.internal.dogstatsd import get_dogstatsd_client
from ddtrace.internal.hostname import get_hostname
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native import PyTracerMetadata
from ddtrace.internal.native import store_metadata
from ddtrace.internal.peer_service.processor import PeerServiceProcessor
from ddtrace.internal.processor.endpoint_call_counter import EndpointCallCounterProcessor
from ddtrace.internal.runtime import get_runtime_id
from ddtrace.internal.sampling import SpanSamplingRule
from ddtrace.internal.sampling import get_span_sampling_rules
from ddtrace.internal.schema.processor import BaseServiceProcessor
from ddtrace.internal.serverless import has_aws_lambda_agent_extension
from ddtrace.internal.serverless import in_aws_lambda
from ddtrace.internal.serverless import in_azure_function
from ddtrace.internal.serverless import in_gcp_function
from ddtrace.internal.service import ServiceStatusError
from ddtrace.internal.utils import _get_metas_to_propagate
from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.internal.utils.http import verify_url
from ddtrace.internal.writer import AgentResponse
from ddtrace.internal.writer import AgentWriter
from ddtrace.internal.writer import LogWriter
from ddtrace.internal.writer import TraceWriter
from ddtrace.settings._agent import config as agent_config
from ddtrace.settings._config import Config
from ddtrace.settings._config import config
from ddtrace.settings.asm import config as asm_config
from ddtrace.settings.peer_service import _ps_config
from ddtrace.version import get_version


log = get_logger(__name__)


AnyCallable = TypeVar("AnyCallable", bound=Callable)

if TYPE_CHECKING:
    from ddtrace.appsec._processor import AppSecSpanProcessor


def _start_appsec_processor() -> Optional["AppSecSpanProcessor"]:
    # FIXME: type should be AppsecSpanProcessor but we have a cyclic import here
    try:
        from ddtrace.appsec._processor import AppSecSpanProcessor

        return AppSecSpanProcessor()
    except Exception as e:
        # DDAS-001-01
        log.error(
            "[DDAS-001-01] "
            "AppSec could not start because of an unexpected error. No security activities will "
            "be collected. "
            "Please contact support at https://docs.datadoghq.com/help/ for help. Error details: "
            "\n%s",
            repr(e),
        )
        if config._raise:
            raise
    return None


def _default_span_processors_factory(
    trace_filters: List[TraceProcessor],
    trace_writer: TraceWriter,
    partial_flush_enabled: bool,
    partial_flush_min_spans: int,
    compute_stats_enabled: bool,
    single_span_sampling_rules: List[SpanSamplingRule],
    agent_url: str,
    trace_sampler: DatadogSampler,
    profiling_span_processor: EndpointCallCounterProcessor,
) -> Tuple[List[SpanProcessor], Optional["AppSecSpanProcessor"], List[SpanProcessor]]:
    # FIXME: type should be AppsecSpanProcessor but we have a cyclic import here
    """Construct the default list of span processors to use."""
    trace_processors: List[TraceProcessor] = []
    trace_processors += [
        PeerServiceProcessor(_ps_config),
        BaseServiceProcessor(),
        TraceSamplingProcessor(
            compute_stats_enabled, trace_sampler, single_span_sampling_rules, asm_config._apm_opt_out
        ),
        TraceTagsProcessor(),
    ]
    trace_processors += trace_filters

    span_processors: List[SpanProcessor] = []
    span_processors += [TopLevelSpanProcessor()]

    if asm_config._asm_libddwaf_available:
        if asm_config._asm_enabled:
            if asm_config._api_security_enabled:
                from ddtrace.appsec._api_security.api_manager import APIManager

                APIManager.enable()

            appsec_processor = _start_appsec_processor()
            if appsec_processor:
                span_processors.append(appsec_processor)
        else:
            # api_security_active will keep track of the service status of APIManager
            # we don't want to import the module if it was not started before due to
            # one click activation of ASM via Remote Config
            if asm_config._api_security_active:
                from ddtrace.appsec._api_security.api_manager import APIManager

                APIManager.disable()

            appsec_processor = None
    else:
        appsec_processor = None

    if asm_config._iast_enabled:
        from ddtrace.appsec._iast.processor import AppSecIastSpanProcessor

        span_processors.append(AppSecIastSpanProcessor())

    if compute_stats_enabled:
        # Inline the import to avoid pulling in ddsketch or protobuf
        # when importing ddtrace.
        from ddtrace.internal.processor.stats import SpanStatsProcessorV06

        span_processors.append(
            SpanStatsProcessorV06(
                agent_url,
            ),
        )

    span_processors.append(profiling_span_processor)

    # These need to run after all the other processors
    deferred_processors: List[SpanProcessor] = [
        SpanAggregator(
            partial_flush_enabled=partial_flush_enabled,
            partial_flush_min_spans=partial_flush_min_spans,
            trace_processors=trace_processors,
            writer=trace_writer,
        )
    ]
    return span_processors, appsec_processor, deferred_processors


class Tracer(object):
    """
    Tracer is used to create, sample and submit spans that measure the
    execution time of sections of code.

    If you're running an application that will serve a single trace per thread,
    you can use the global tracer instance::

        from ddtrace.trace import tracer
        trace = tracer.trace('app.request', 'web-server').finish()
    """

    SHUTDOWN_TIMEOUT = 5
    _instance = None

    def __init__(self) -> None:
        """
        Create a new ``Tracer`` instance. A global tracer is already initialized
        for common usage, so there is no need to initialize your own ``Tracer``.
        """

        # Do not set self._instance if this is a subclass of Tracer. Here we only want
        # to reference the global instance.
        if type(self) is Tracer:
            if Tracer._instance is None:
                Tracer._instance = self
            else:
                log.error(
                    "Initializing multiple Tracer instances is not supported. Use ``ddtrace.trace.tracer`` instead.",
                )

        self._user_trace_processors: List[TraceProcessor] = []

        # globally set tags
        self._tags = config.tags.copy()

        # Runtime id used for associating data collected during runtime to
        # traces
        self._pid = getpid()

        self.enabled = config._tracing_enabled
        self.context_provider: BaseContextProvider = DefaultContextProvider()
        self._dogstatsd_url = agent_config.dogstatsd_url
        if asm_config._apm_opt_out:
            self.enabled = False
            # Disable compute stats (neither agent or tracer should compute them)
            config._trace_compute_stats = False
            # If ASM is enabled but tracing is disabled,
            # we need to set the rate limiting to 1 trace per minute
            # for the backend to consider the service as alive.
            self._sampler = DatadogSampler(rate_limit=1, rate_limit_window=60e9, rate_limit_always_on=True)
        else:
            self._sampler = DatadogSampler()
        self._compute_stats = config._trace_compute_stats
        self._agent_url: str = agent_config.trace_agent_url
        verify_url(self._agent_url)

        if self._use_log_writer():
            writer: TraceWriter = LogWriter()
        else:
            writer = AgentWriter(
                agent_url=self._agent_url,
                dogstatsd=get_dogstatsd_client(self._dogstatsd_url),
                sync_mode=self._use_sync_mode(),
                headers={"Datadog-Client-Computed-Stats": "yes"}
                if (self._compute_stats or asm_config._apm_opt_out)
                else {},
                report_metrics=not asm_config._apm_opt_out,
                response_callback=self._agent_response_callback,
            )
        self._single_span_sampling_rules: List[SpanSamplingRule] = get_span_sampling_rules()
        self._writer: TraceWriter = writer
        self._partial_flush_enabled = config._partial_flush_enabled
        self._partial_flush_min_spans = config._partial_flush_min_spans
        # Direct link to the appsec processor
        self._endpoint_call_counter_span_processor = EndpointCallCounterProcessor()
        self._span_processors, self._appsec_processor, self._deferred_processors = _default_span_processors_factory(
            self._user_trace_processors,
            self._writer,
            self._partial_flush_enabled,
            self._partial_flush_min_spans,
            self._compute_stats,
            self._single_span_sampling_rules,
            self._agent_url,
            self._sampler,
            self._endpoint_call_counter_span_processor,
        )
        if config._data_streams_enabled:
            # Inline the import to avoid pulling in ddsketch or protobuf
            # when importing ddtrace.
            from ddtrace.internal.datastreams.processor import DataStreamsProcessor

            self.data_streams_processor = DataStreamsProcessor(self._agent_url)
            register_on_exit_signal(self._atexit)

        self._hooks = Hooks()
        forksafe.register_before_fork(self._sample_before_fork)

        # Non-global tracers require that we still register these hooks, until
        # their usage is fully deprecated. The global one will be managed by the
        # product protocol. We also need to register these hooks if the library
        # was not bootstrapped correctly.
        if not isinstance(self, Tracer) or "ddtrace.bootstrap.sitecustomize" not in sys.modules:
            atexit.register(self._atexit)
            forksafe.register(self._child_after_fork)

        self._shutdown_lock = RLock()

        self._new_process = False
        config._subscribe(["_trace_sampling_rules"], self._on_global_config_update)
        config._subscribe(["_logs_injection"], self._on_global_config_update)
        config._subscribe(["tags"], self._on_global_config_update)
        config._subscribe(["_tracing_enabled"], self._on_global_config_update)

        metadata = PyTracerMetadata(
            runtime_id=get_runtime_id(),
            tracer_version=get_version(),
            hostname=get_hostname(),
            service_name=config.service or None,
            service_env=config.env or None,
            service_version=config.version or None,
        )
        try:
            self._config_on_disk = store_metadata(metadata)
        except Exception as e:
            log.debug("Failed to store the configuration on disk", extra=dict(error=e))

    def _atexit(self) -> None:
        key = "ctrl-break" if os.name == "nt" else "ctrl-c"
        log.debug(
            "Waiting %d seconds for tracer to finish. Hit %s to quit.",
            self.SHUTDOWN_TIMEOUT,
            key,
        )
        self.shutdown(timeout=self.SHUTDOWN_TIMEOUT)

    def sample(self, span):
        if self._sampler is not None:
            self._sampler.sample(span)
        else:
            log.error("No sampler available to sample span")

    def on_start_span(self, func: Callable) -> Callable:
        """Register a function to execute when a span start.

        Can be used as a decorator.

        :param func: The function to call when starting a span.
                     The started span will be passed as argument.
        """
        self._hooks.register(self.__class__.start_span, func)
        return func

    def deregister_on_start_span(self, func: Callable) -> Callable:
        """Unregister a function registered to execute when a span starts.

        Can be used as a decorator.

        :param func: The function to stop calling when starting a span.
        """

        self._hooks.deregister(self.__class__.start_span, func)
        return func

    def _sample_before_fork(self) -> None:
        span = self.current_root_span()
        if span is not None and span.context.sampling_priority is None:
            self.sample(span)

    @contextmanager
    def _activate_context(self, context: Context):
        prev_active = self.context_provider.active()
        context._reactivate = True
        self.context_provider.activate(context)
        try:
            yield
        finally:
            context._reactivate = False
            self.context_provider.activate(prev_active)

    @property
    def _sampler(self):
        return self._sampler_current

    @_sampler.setter
    def _sampler(self, value):
        self._sampler_current = value
        # we need to update the processor that uses the sampler
        if getattr(self, "_deferred_processors", None):
            for aggregator in self._deferred_processors:
                if type(aggregator) == SpanAggregator:
                    for processor in aggregator._trace_processors:
                        if type(processor) == TraceSamplingProcessor:
                            processor.sampler = value
                            break
            else:
                log.debug("No TraceSamplingProcessor available to update sampling rate")

    @property
    def debug_logging(self):
        return log.isEnabledFor(logging.DEBUG)

    def current_trace_context(self, *args, **kwargs) -> Optional[Context]:
        """Return the context for the current trace.

        If there is no active trace then None is returned.
        """
        active = self.context_provider.active()
        if isinstance(active, Context):
            return active
        elif isinstance(active, Span):
            return active.context
        return None

    def get_log_correlation_context(self, active: Optional[Union[Context, Span]] = None) -> Dict[str, str]:
        """Retrieves the data used to correlate a log with the current active trace.
        Generates a dictionary for custom logging instrumentation including the trace id and
        span id of the current active span, as well as the configured service, version, and environment names.
        If there is no active span, a dictionary with an empty string for each value will be returned.
        """
        if active is None and (self.enabled or asm_config._apm_opt_out):
            active = self.context_provider.active()

        if isinstance(active, Span) and active.service:
            service = active.service
        else:
            service = config.service

        span_id = "0"
        trace_id = "0"
        if active:
            span_id = str(active.span_id) if active.span_id else span_id
            trace_id = format_trace_id(active.trace_id) if active.trace_id else trace_id

        return {
            "trace_id": trace_id,
            "span_id": span_id,
            "service": service or "",
            "version": config.version or "",
            "env": config.env or "",
        }

    def configure(
        self,
        context_provider: Optional[BaseContextProvider] = None,
        compute_stats_enabled: Optional[bool] = None,
        appsec_enabled: Optional[bool] = None,
        iast_enabled: Optional[bool] = None,
        apm_tracing_disabled: Optional[bool] = None,
        trace_processors: Optional[List[TraceProcessor]] = None,
    ) -> None:
        """Configure a Tracer.

        :param object context_provider: The ``ContextProvider`` that will be used to retrieve
            automatically the current call context. This is an advanced option that usually
            doesn't need to be changed from the default value.
        :param bool appsec_enabled: Enables Application Security Monitoring (ASM) for the tracer.
        :param bool iast_enabled: Enables IAST support for the tracer
        :param bool apm_tracing_disabled: When APM tracing is disabled ensures ASM support is still enabled.
        :param List[TraceProcessor] trace_processors: This parameter sets TraceProcessor (ex: TraceFilters).
           Trace processors are used to modify and filter traces based on certain criteria.
        """

        if appsec_enabled is not None:
            asm_config._asm_enabled = appsec_enabled

        if iast_enabled is not None:
            asm_config._iast_enabled = iast_enabled

        if apm_tracing_disabled is not None:
            asm_config._apm_tracing_enabled = not apm_tracing_disabled

        if asm_config._apm_opt_out:
            self.enabled = False
            # Disable compute stats (neither agent or tracer should compute them)
            config._trace_compute_stats = False
            # Update the rate limiter to 1 trace per minute when tracing is disabled
            self._sampler = DatadogSampler(rate_limit=1, rate_limit_window=60e9, rate_limit_always_on=True)
            log.debug("ASM standalone mode is enabled, traces will be rate limited at 1 trace per minute")

        if compute_stats_enabled is not None:
            self._compute_stats = compute_stats_enabled

        if isinstance(self._writer, AgentWriter):
            if appsec_enabled:
                self._writer._api_version = "v0.4"
            self._writer.dogstatsd = get_dogstatsd_client(self._dogstatsd_url)

        if trace_processors:
            self._user_trace_processors = trace_processors

        if any(
            x is not None
            for x in [
                trace_processors,
                compute_stats_enabled,
                appsec_enabled,
                iast_enabled,
            ]
        ):
            self._recreate()

        if context_provider is not None:
            self.context_provider = context_provider

        self._generate_diagnostic_logs()

    def _agent_response_callback(self, resp: AgentResponse) -> None:
        """Handle the response from the agent.

        The agent can return updated sample rates for the priority sampler.
        """
        try:
            self._sampler.update_rate_by_service_sample_rates(
                resp.rate_by_service,
            )
        except ValueError as e:
            log.error("Failed to set agent service sample rates: %s", str(e))

    def _generate_diagnostic_logs(self):
        if config._debug_mode or config._startup_logs_enabled:
            try:
                info = debug.collect(self)
            except Exception as e:
                msg = "Failed to collect start-up logs: %s" % e
                self._log_compat(logging.WARNING, "- DATADOG TRACER DIAGNOSTIC - %s" % msg)
            else:
                if log.isEnabledFor(logging.INFO):
                    msg = "- DATADOG TRACER CONFIGURATION - %s" % info
                    self._log_compat(logging.INFO, msg)

                # Always log errors since we're either in debug_mode or start up logs
                # are enabled.
                agent_error = info.get("agent_error")
                if agent_error:
                    msg = "- DATADOG TRACER DIAGNOSTIC - %s" % agent_error
                    self._log_compat(logging.WARNING, msg)

    def _child_after_fork(self):
        self._pid = getpid()
        self._recreate()
        self._new_process = True

    def _recreate(self):
        """Re-initialize the tracer's processors and trace writer. This method should only be used in tests."""
        # Stop the writer.
        # This will stop the periodic thread in HTTPWriters, preventing memory leaks and unnecessary I/O.
        try:
            self._writer.stop()
        except ServiceStatusError:
            # Some writers (ex: AgentWriter), start when the first trace chunk is encoded. Stopping
            # the writer before that point will raise a ServiceStatusError.
            pass
        # Re-create the background writer thread
        self._writer = self._writer.recreate()
        # Recreate the trace and span processors
        self._span_processors, self._appsec_processor, self._deferred_processors = _default_span_processors_factory(
            self._user_trace_processors,
            self._writer,
            self._partial_flush_enabled,
            self._partial_flush_min_spans,
            self._compute_stats,
            self._single_span_sampling_rules,
            self._agent_url,
            self._sampler,
            self._endpoint_call_counter_span_processor,
        )

    def _start_span_after_shutdown(
        self,
        name: str,
        child_of: Optional[Union[Span, Context]] = None,
        service: Optional[str] = None,
        resource: Optional[str] = None,
        span_type: Optional[str] = None,
        activate: bool = False,
        span_api: str = SPAN_API_DATADOG,
    ) -> Span:
        log.warning("Spans started after the tracer has been shut down will not be sent to the Datadog Agent.")
        return self._start_span(name, child_of, service, resource, span_type, activate, span_api)

    def _start_span(
        self,
        name: str,
        child_of: Optional[Union[Span, Context]] = None,
        service: Optional[str] = None,
        resource: Optional[str] = None,
        span_type: Optional[str] = None,
        activate: bool = False,
        span_api: str = SPAN_API_DATADOG,
    ) -> Span:
        """Return a span that represents an operation called ``name``.

        Note that the :meth:`.trace` method will almost always be preferred
        over this method as it provides automatic span parenting. This method
        should only be used if manual parenting is desired.

        :param str name: the name of the operation being traced.
        :param object child_of: a ``Span`` or a ``Context`` instance representing the parent for this span.
        :param str service: the name of the service being traced.
        :param str resource: an optional name of the resource being tracked.
        :param str span_type: an optional operation type.
        :param activate: activate the span once it is created.

        To start a new root span::

            span = tracer.start_span("web.request")

        To create a child for a root span::

            root_span = tracer.start_span("web.request")
            span = tracer.start_span("web.decoder", child_of=root_span)

        Spans from ``start_span`` are not activated by default::

            with tracer.start_span("parent") as parent:
                assert tracer.current_span() is None
                with tracer.start_span("child", child_of=parent):
                    assert tracer.current_span() is None

            new_parent = tracer.start_span("new_parent", activate=True)
            assert tracer.current_span() is new_parent

        Note: be sure to finish all spans to avoid memory leaks and incorrect
        parenting of spans.
        """
        if self._new_process:
            self._new_process = False

            # The spans remaining in the context can not and will not be
            # finished in this new process. So to avoid memory leaks the
            # strong span reference (which will never be finished) is replaced
            # with a context representing the span.
            if isinstance(child_of, Span):
                new_ctx = child_of.context
                # If the child_of span was active then activate the new context
                # containing it so that the strong span referenced is removed
                # from the execution.
                if self.context_provider.active() is child_of:
                    self.context_provider.activate(new_ctx)
                child_of = new_ctx

        parent: Optional[Span] = None
        if child_of is not None:
            if isinstance(child_of, Context):
                context = child_of
            else:
                context = child_of.context
                parent = child_of
        else:
            context = Context(is_remote=False)

        trace_id = context.trace_id
        parent_id = context.span_id

        # The following precedence is used for a new span's service:
        # 1. Explicitly provided service name
        #     a. User provided or integration provided service name
        # 2. Parent's service name (if defined)
        # 3. Globally configured service name
        #     a. `config.service`/`DD_SERVICE`/`DD_TAGS`
        if service is None:
            if parent:
                service = parent.service
            else:
                service = config.service

        # Update the service name based on any mapping
        service = config.service_mapping.get(service, service)

        links = context._span_links if not parent else []
        if trace_id or links or context._baggage:
            # child_of a non-empty context, so either a local child span or from a remote context
            span = Span(
                name=name,
                context=context,
                trace_id=trace_id,
                parent_id=parent_id,
                service=service,
                resource=resource,
                span_type=span_type,
                span_api=span_api,
                links=links,
                on_finish=[self._on_span_finish],
            )

            # Extra attributes when from a local parent
            if parent:
                span._parent = parent
                span._local_root = parent._local_root

            for k, v in _get_metas_to_propagate(context):
                # We do not want to propagate AppSec propagation headers
                # to children spans, only across distributed spans
                if k not in (SAMPLING_DECISION_TRACE_TAG_KEY, APPSEC.PROPAGATION_HEADER):
                    span._meta[k] = v
        else:
            # this is the root span of a new trace
            span = Span(
                name=name,
                context=context,
                service=service,
                resource=resource,
                span_type=span_type,
                span_api=span_api,
                on_finish=[self._on_span_finish],
            )
            if config._report_hostname:
                span.set_tag_str(_HOSTNAME_KEY, hostname.get_hostname())

        if not span._parent:
            span.set_tag_str("runtime-id", get_runtime_id())
            span._metrics[PID] = self._pid

        # Apply default global tags.
        if self._tags:
            span.set_tags(self._tags)

        if config.env:
            span.set_tag_str(ENV_KEY, config.env)

        # Only set the version tag on internal spans.
        if config.version:
            root_span = self.current_root_span()
            # if: 1. the span is the root span and the span's service matches the global config; or
            #     2. the span is not the root, but the root span's service matches the span's service
            #        and the root span has a version tag
            # then the span belongs to the user application and so set the version tag
            if (root_span is None and service == config.service) or (
                root_span and root_span.service == service and root_span.get_tag(VERSION_KEY) is not None
            ):
                span.set_tag_str(VERSION_KEY, config.version)

        if activate:
            self.context_provider.activate(span)

        # Only call span processors if the tracer is enabled (even if APM opted out)
        if self.enabled or asm_config._apm_opt_out:
            for p in chain(self._span_processors, SpanProcessor.__processors__, self._deferred_processors):
                p.on_span_start(span)
        self._hooks.emit(self.__class__.start_span, span)
        dispatch("trace.span_start", (span,))
        return span

    start_span = _start_span

    def _on_span_finish(self, span: Span) -> None:
        active = self.current_span()
        # Debug check: if the finishing span has a parent and its parent
        # is not the next active span then this is an error in synchronous tracing.
        if span._parent is not None and active is not span._parent:
            log.debug("span %r closing after its parent %r, this is an error when not using async", span, span._parent)

        # Only call span processors if the tracer is enabled (even if APM opted out)
        if self.enabled or asm_config._apm_opt_out:
            for p in chain(self._span_processors, SpanProcessor.__processors__, self._deferred_processors):
                p.on_span_finish(span)

        dispatch("trace.span_finish", (span,))

        if log.isEnabledFor(logging.DEBUG):
            log.debug("finishing span %s (enabled:%s)", span._pprint(), self.enabled)

    def _log_compat(self, level, msg):
        """Logs a message for the given level.

        Instead, something like this will be printed to stderr:
            No handlers could be found for logger "ddtrace.tracer"

        Since the global tracer is configured on import and it is recommended
        to import the tracer as early as possible, it will likely be the case
        that there are no handlers installed yet.
        """
        log.log(level, msg)

    def trace(
        self,
        name: str,
        service: Optional[str] = None,
        resource: Optional[str] = None,
        span_type: Optional[str] = None,
        span_api: str = SPAN_API_DATADOG,
    ) -> Span:
        """Activate and return a new span that inherits from the current active span.

        :param str name: the name of the operation being traced
        :param str service: the name of the service being traced. If not set,
                            it will inherit the service from its parent.
        :param str resource: an optional name of the resource being tracked.
        :param str span_type: an optional operation type.

        The returned span *must* be ``finish``'d or it will remain in memory
        indefinitely::

            >>> span = tracer.trace("web.request")
                try:
                    # do something
                finally:
                    span.finish()

            >>> with tracer.trace("web.request") as span:
                    # do something

        Example of the automatic parenting::

            parent = tracer.trace("parent")     # has no parent span
            assert tracer.current_span() is parent

            child  = tracer.trace("child")
            assert child.parent_id == parent.span_id
            assert tracer.current_span() is child
            child.finish()

            # parent is now the active span again
            assert tracer.current_span() is parent
            parent.finish()

            assert tracer.current_span() is None

            parent2 = tracer.trace("parent2")
            assert parent2.parent_id is None
            parent2.finish()
        """
        return self.start_span(
            name,
            child_of=self.context_provider.active(),
            service=service,
            resource=resource,
            span_type=span_type,
            activate=True,
            span_api=span_api,
        )

    def current_root_span(self) -> Optional[Span]:
        """Returns the local root span of the current execution/process.

        Note: This cannot be used to access the true root span of the trace
        in a distributed tracing setup if the actual root span occurred in
        another execution/process.

        This is useful for attaching information to the local root span
        of the current execution/process, which is often also service
        entry span.

        For example::

            # get the local root span
            local_root_span = tracer.current_root_span()
            # set the host just once on the root span
            if local_root_span:
                local_root_span.set_tag('host', '127.0.0.1')
        """
        span = self.current_span()
        if span is None:
            return None
        return span._local_root

    def current_span(self) -> Optional[Span]:
        """Return the active span in the current execution context.

        Note that there may be an active span represented by a context object
        (like from a distributed trace) which will not be returned by this
        method.
        """
        active = self.context_provider.active()
        return active if isinstance(active, Span) else None

    @property
    def agent_trace_url(self) -> Optional[str]:
        """Trace agent url"""
        if isinstance(self._writer, AgentWriter):
            return self._writer.agent_url

        return None

    def flush(self):
        """Flush the buffer of the trace writer. This does nothing if an unbuffered trace writer is used."""
        self._writer.flush_queue()

    def wrap(
        self,
        name: Optional[str] = None,
        service: Optional[str] = None,
        resource: Optional[str] = None,
        span_type: Optional[str] = None,
    ) -> Callable[[AnyCallable], AnyCallable]:
        """
        A decorator used to trace an entire function. If the traced function
        is a coroutine, it traces the coroutine execution when is awaited.

        :param str name: the name of the operation being traced. If not set,
                         defaults to the fully qualified function name.
        :param str service: the name of the service being traced. If not set,
                            it will inherit the service from it's parent.
        :param str resource: an optional name of the resource being tracked.
        :param str span_type: an optional operation type.

        >>> @tracer.wrap('my.wrapped.function', service='my.service')
            def run():
                return 'run'

        >>> # name will default to 'execute' if unset
            @tracer.wrap()
            def execute():
                return 'executed'

        >>> # or use it in asyncio coroutines
            @tracer.wrap()
            async def coroutine():
                return 'executed'

        >>> @tracer.wrap()
            @asyncio.coroutine
            def coroutine():
                return 'executed'

        You can access the current span using `tracer.current_span()` to set
        tags:

        >>> @tracer.wrap()
            def execute():
                span = tracer.current_span()
                span.set_tag('a', 'b')
        """

        def wrap_decorator(f: AnyCallable) -> AnyCallable:
            # FIXME[matt] include the class name for methods.
            span_name = name if name else "%s.%s" % (f.__module__, f.__name__)

            # detect if the the given function is a coroutine to use the
            # right decorator; this initial check ensures that the
            # evaluation is done only once for each @tracer.wrap
            if iscoroutinefunction(f):
                # call the async factory that creates a tracing decorator capable
                # to await the coroutine execution before finishing the span. This
                # code is used for compatibility reasons to prevent Syntax errors
                # in Python 2
                func_wrapper = compat.make_async_decorator(
                    self,
                    f,
                    span_name,
                    service=service,
                    resource=resource,
                    span_type=span_type,
                )
            else:

                @functools.wraps(f)
                def func_wrapper(*args, **kwargs):
                    # if a wrap executor has been configured, it is used instead
                    # of the default tracing function
                    if getattr(self, "_wrap_executor", None):
                        return self._wrap_executor(
                            self,
                            f,
                            args,
                            kwargs,
                            span_name,
                            service=service,
                            resource=resource,
                            span_type=span_type,
                        )

                    # otherwise fallback to a default tracing
                    with self.trace(span_name, service=service, resource=resource, span_type=span_type):
                        return f(*args, **kwargs)

            return func_wrapper

        return wrap_decorator

    def set_tags(self, tags: Dict[str, str]) -> None:
        """Set some tags at the tracer level.
        This will append those tags to each span created by the tracer.

        :param dict tags: dict of tags to set at tracer level
        """
        self._tags.update(tags)

    def shutdown(self, timeout: Optional[float] = None) -> None:
        """Shutdown the tracer and flush finished traces. Avoid calling shutdown multiple times.

        :param timeout: How long in seconds to wait for the background worker to flush traces
            before exiting or :obj:`None` to block until flushing has successfully completed (default: :obj:`None`)
        :type timeout: :obj:`int` | :obj:`float` | :obj:`None`
        """
        with self._shutdown_lock:
            # Thread safety: Ensures tracer is shutdown synchronously
            span_processors = self._span_processors
            deferred_processors = self._deferred_processors
            self._span_processors = []
            self._deferred_processors = []

            for processor in chain(span_processors, SpanProcessor.__processors__, deferred_processors):
                if hasattr(processor, "shutdown"):
                    processor.shutdown(timeout)
            forksafe.unregister_before_fork(self._sample_before_fork)
            # Non-global tracers require that we still register these hooks,
            # until their usage is fully deprecated. The global one will be
            # managed by the product protocol. We also need to register these
            # hooks if the library was not bootstrapped correctly.
            if not isinstance(self, Tracer) or "ddtrace.bootstrap.sitecustomize" not in sys.modules:
                atexit.unregister(self._atexit)
                forksafe.unregister(self._child_after_fork)

        self.start_span = self._start_span_after_shutdown  # type: ignore[assignment]

    @staticmethod
    def _use_log_writer() -> bool:
        """Returns whether the LogWriter should be used in the environment by
        default.

        The LogWriter required by default in AWS Lambdas when the Datadog Agent extension
        is not available in the Lambda.
        """
        if (
            environ.get("DD_AGENT_HOST")
            or environ.get("DATADOG_TRACE_AGENT_HOSTNAME")
            or environ.get("DD_TRACE_AGENT_URL")
        ):
            # If one of these variables are set, we definitely have an agent
            return False
        elif in_aws_lambda() and has_aws_lambda_agent_extension():
            # If the Agent Lambda extension is available then an AgentWriter is used.
            return False
        elif in_gcp_function() or in_azure_function():
            return False
        else:
            return in_aws_lambda()

    @staticmethod
    def _use_sync_mode() -> bool:
        """Returns, if an `AgentWriter` is to be used, whether it should be run
         in synchronous mode by default.

        There are only two cases in which this is desirable:

        - AWS Lambdas can have the Datadog agent installed via an extension.
          When it's available traces must be sent synchronously to ensure all
          are received before the Lambda terminates.
        - Google Cloud Functions and Azure Functions have a mini-agent spun up by the tracer.
          Similarly to AWS Lambdas, sync mode should be used to avoid data loss.
        """
        return (in_aws_lambda() and has_aws_lambda_agent_extension()) or in_gcp_function() or in_azure_function()

    def _on_global_config_update(self, cfg: Config, items: List[str]) -> None:
        # sampling configs always come as a pair
        if "_trace_sampling_rules" in items:
            self._sampler.set_sampling_rules(cfg._trace_sampling_rules)

        if "tags" in items:
            self._tags = cfg.tags.copy()

        if "_tracing_enabled" in items:
            if self.enabled:
                if cfg._tracing_enabled is False:
                    self.enabled = False
            else:
                # the product specification says not to allow tracing to be re-enabled remotely at runtime
                if cfg._tracing_enabled is True and cfg._get_source("_tracing_enabled") != "remote_config":
                    self.enabled = True

        if "_logs_injection" in items:
            if config._logs_injection:
                from ddtrace.contrib.internal.logging.patch import patch

                patch()
            else:
                from ddtrace.contrib.internal.logging.patch import unpatch

                unpatch()
