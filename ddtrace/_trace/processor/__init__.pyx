# cython: freethreading_compatible=True
from collections import defaultdict
from itertools import chain
import logging
from threading import RLock
from typing import DefaultDict
from typing import List
from typing import Optional

from ddtrace._trace.sampler import DatadogSampler
from ddtrace._trace.span import Span
from ddtrace._trace.span import _get_64_highest_order_bits_as_hex
from ddtrace.constants import _APM_ENABLED_METRIC_KEY as MK_APM_ENABLED
from ddtrace.constants import _SINGLE_SPAN_SAMPLING_MECHANISM
from ddtrace.internal import gitmetadata
from ddtrace.internal import telemetry
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.constants import HIGHER_ORDER_TRACE_ID_BITS
from ddtrace.internal.constants import LAST_DD_PARENT_ID_KEY
from ddtrace.internal.constants import MAX_UINT_64BITS
from ddtrace.internal.constants import SAMPLING_DECISION_TRACE_TAG_KEY
from ddtrace.internal.constants import SamplingMechanism
from ddtrace.internal.logger import get_logger
from ddtrace.internal.rate_limiter import RateLimiter
from ddtrace.internal.sampling import SpanSamplingRule
from ddtrace.internal.sampling import get_span_sampling_rules
from ddtrace.internal.service import ServiceStatusError
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.internal.writer import AgentResponse
from ddtrace.internal.writer import create_trace_writer
from ddtrace.settings._config import config
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)


cdef class TraceProcessor:
    def __init__(self) -> None:
        """Default post initializer which logs the representation of the
        TraceProcessor at the ``logging.DEBUG`` level.
        """
        pass

    cpdef object process_trace(self, list trace):
        """Processes a trace.

        ``None`` can be returned to prevent the trace from being further
        processed.
        """
        pass


cdef class SpanProcessor:
    """A Processor is used to process spans as they are created and finished by a tracer."""

    __processors__: List["SpanProcessor"] = []

    def __init__(self) -> None:
        """Default post initializer which logs the representation of the
        Processor at the ``logging.DEBUG`` level.
        """
        pass

    def on_span_start(self, span: Span) -> None:
        """Called when a span is started.

        This method is useful for making upfront decisions on spans.

        For example, a sampling decision can be made when the span is created
        based on its resource name.
        """
        pass

    def on_span_finish(self, span: Span) -> None:
        """Called with the result of any previous processors or initially with
        the finishing span when a span finishes.

        It can return any data which will be passed to any processors that are
        applied afterwards.
        """
        pass

    def shutdown(self, timeout: Optional[float]) -> None:
        """Called when the processor is done being used.

        Any clean-up or flushing should be performed with this method.
        """
        pass

    def register(self) -> None:
        """Register the processor with the global list of processors."""
        SpanProcessor.__processors__.append(self)

    def unregister(self) -> None:
        """Unregister the processor from the global list of processors."""
        try:
            SpanProcessor.__processors__.remove(self)
        except ValueError:
            log.warning("Span processor %r not registered", self)


cdef class TraceSamplingProcessor(TraceProcessor):
    """Processor that runs both trace and span sampling rules.

    * Span sampling must be applied after trace sampling priority has been set.
    * Span sampling rules are specified with a sample rate or rate limit as well as glob patterns
      for matching spans on service and name.
    * If the span sampling decision is to keep the span, then span sampling metrics are added to the span.
    * If a dropped trace includes a span that had been kept by a span sampling rule, then the span is sent to the
      Agent even if the dropped trace is not (as is the case when trace stats computation is enabled).
    """

    # TODO: Make these not public anymore
    cdef public bint _compute_stats_enabled, _apm_opt_out
    cdef public list single_span_rules
    cdef public object sampler

    def __init__(
        self,
        compute_stats_enabled: bool,
        single_span_rules: List[SpanSamplingRule],
        apm_opt_out: bool,
    ):
        super(TraceSamplingProcessor, self).__init__()
        self._compute_stats_enabled = compute_stats_enabled
        self.single_span_rules = single_span_rules
        self.sampler = DatadogSampler()
        self._apm_opt_out = apm_opt_out

    @property
    def apm_opt_out(self):
        return self._apm_opt_out

    @apm_opt_out.setter
    def apm_opt_out(self, value):
        # If ASM is enabled but tracing is disabled,
        # we need to set the rate limiting to 1 trace per minute
        # for the backend to consider the service as alive.
        if value:
            self.sampler.limiter = RateLimiter(rate_limit=1, time_window=60e9)
            self.sampler._rate_limit_always_on = True
            log.debug("Enabling apm opt out on DatadogSampler: %s", self.sampler)
        else:
            self.sampler.limiter = RateLimiter(rate_limit=int(config._trace_rate_limit), time_window=1e9)
            self.sampler._rate_limit_always_on = False
        self._apm_opt_out = value

    cpdef object process_trace(self, list trace):
        cdef object chunk_root, span
        cdef list single_spans
        cdef bint can_drop_trace

        if trace:
            chunk_root = trace[0]

            if self.apm_opt_out:
                for span in trace:
                    if span._local_root_value is None:
                        span.set_metric(MK_APM_ENABLED, 0)

            if chunk_root.context.sampling_priority is None:
                self.sampler.sample(chunk_root._local_root)
                if chunk_root.context.sampling_priority is None:
                    # NOTE: This should never happen, `self.sampler.sample(..)` should always set the sampling priority.
                    log.error(
                        "DatadogSampler failed to sample trace. Local Root: %s",
                        chunk_root._local_root,
                    )
                    return trace

            # single span sampling rules are applied if the trace is about to be dropped
            if self.single_span_rules and chunk_root.context.sampling_priority <= 0:
                single_spans = []
                # When stats computation is enabled in the tracer then we can safely drop the traces.
                # When using the NativeWriter this is handled by native code.
                can_drop_trace = (
                    not config._trace_writer_native and self._compute_stats_enabled and not self.apm_opt_out
                )
                for span in trace:
                    for rule in self.single_span_rules:
                        if rule.match(span):
                            # Sampling a span here does NOT effect the sampling priotiy. This operation
                            # simply marks a span as single-span sampled.
                            rule.sample(span)
                            if can_drop_trace:
                                single_spans.append(span)
                            break
                if can_drop_trace:
                    return single_spans
            return trace
        return None


cdef class TopLevelSpanProcessor(SpanProcessor):
    """Processor marks spans as top level

    A span is top level when it is the entrypoint method for a request to a service.
    Top level span and service entry span are equivalent terms

    The "top level" metric will be used by the agent to calculate trace metrics
    and determine how spans should be displaced in the UI. If this metric is not
    set by the tracer the first span in a trace chunk will be marked as top level.

    """

    cpdef void on_span_start(self, _: Span):
        pass

    cpdef void on_span_finish(self, span: Span):
        # DEV: Update span after finished to avoid race condition
        if span._is_top_level:
            span._metrics["_dd.top_level"] = 1  # PERF: avoid setting via Span.set_metric


cdef class ServiceNameProcessor(TraceProcessor):
    """Processor that adds the service name to the globalconfig."""

    cpdef object process_trace(self, list trace):
        cdef object span
        for span in trace:
            if span.service:
                config._add_extra_service(span.service)
        return trace


cdef class TraceTagsProcessor(TraceProcessor):
    """Processor that applies trace-level tags to the trace."""

    cdef void _set_git_metadata(self, chunk_root: Span) except *:
        cdef str repository_url, commit_sha, main_package
        repository_url, commit_sha, main_package = gitmetadata.get_git_tags()
        if repository_url:
            chunk_root.set_tag_str("_dd.git.repository_url", repository_url)
        if commit_sha:
            chunk_root.set_tag_str("_dd.git.commit.sha", commit_sha)
        if main_package:
            chunk_root.set_tag_str("_dd.python_main_package", main_package)

    cpdef object process_trace(self, list trace):
        cdef list spans_to_tag
        cdef object span
        cdef str trace_id_hob

        if not trace:
            return trace

        spans_to_tag = [trace[0]]

        # When using the native writer and CSS, TraceTagsProcessor runs before dropping spans.
        # Thus trace tags are applied to a root span which may be dropped by sampling, even though
        # some spans of the chunk are sampled. We prevent it by adding trace tags to the first
        # single-sampled span of the chunk.
        if config._trace_compute_stats and config._trace_writer_native:
            for span in trace:
                if span.get_metric(_SINGLE_SPAN_SAMPLING_MECHANISM) == SamplingMechanism.SPAN_SAMPLING_RULE:
                    spans_to_tag.append(span)
                    break

        for span in spans_to_tag:
            span._update_tags_from_context()
            self._set_git_metadata(span)
            span.set_tag_str("language", "python")
            # for 128 bit trace ids
            if span.trace_id > MAX_UINT_64BITS:
                trace_id_hob = _get_64_highest_order_bits_as_hex(span.trace_id)
                span.set_tag_str(HIGHER_ORDER_TRACE_ID_BITS, trace_id_hob)

            if LAST_DD_PARENT_ID_KEY in span._meta and span._parent is not None:
                # we should only set the last parent id on local root spans
                del span._meta[LAST_DD_PARENT_ID_KEY]

        return trace


cdef class _Trace:
    cdef list spans
    cdef int num_finished

    def __init__(self, spans: Optional[List[Span]] = None, num_finished: int = 0) -> None:
        self.spans: List[Span] = spans if spans is not None else []
        self.num_finished: int = num_finished

    cdef list remove_finished(self):
        cdef object s
        cdef list finished
        # perf: Avoid Span.finished which is a computed property and has function call overhead
        #       so check Span.duration_ns manually.
        finished = [s for s in self.spans if s.duration_ns is not None]
        if finished:
            self.spans[:] = [s for s in self.spans if s.duration_ns is None]
            self.num_finished = 0
        return finished


cdef class SpanAggregator(SpanProcessor):
    """Processor that aggregates spans together by trace_id and writes the
    spans to the provided writer when:
        - The collection is assumed to be complete. A collection of spans is
          assumed to be complete if all the spans that have been created with
          the trace_id have finished; or
        - A minimum threshold of spans (``partial_flush_min_spans``) have been
          finished in the collection and ``partial_flush_enabled`` is True.
    """

    SPAN_FINISH_DEBUG_MESSAGE = (
        "Encoding %d spans. Spans processed: %d. Spans dropped by trace processors: %d. Unfinished "
        "spans remaining in the span aggregator: %d. (trace_id: %d) (top level span: name=%s) "
        "(sampling_priority: %s) (sampling_mechanism: %s) (partial flush triggered: %s)"
    )

    SPAN_START_DEBUG_MESSAGE = "Starting span: %s, trace has %d spans in the span aggregator"

    # TODO: Make these not public anymore
    cdef public bint partial_flush_enabled
    cdef public int partial_flush_min_spans
    cdef public object sampling_processor, tags_processor, service_name_processor
    cdef public list dd_processors, user_processors
    cdef public object writer
    cdef public dict _traces, _spans_created, _spans_finished
    cdef public object _lock
    cdef public int _total_spans_finished

    def __init__(
        self,
        partial_flush_enabled: bool,
        partial_flush_min_spans: int,
        dd_processors: Optional[List[TraceProcessor]] = None,
        user_processors: Optional[List[TraceProcessor]] = None,
    ):
        # Set partial flushing
        self.partial_flush_enabled = partial_flush_enabled
        self.partial_flush_min_spans = partial_flush_min_spans
        # Initialize trace processors
        self.sampling_processor = TraceSamplingProcessor(
            config._trace_compute_stats, get_span_sampling_rules(), asm_config._apm_opt_out
        )
        self.tags_processor = TraceTagsProcessor()
        self.dd_processors = dd_processors or []
        self.user_processors = user_processors or []
        self.service_name_processor = ServiceNameProcessor()
        self.writer = create_trace_writer(response_callback=self._agent_response_callback)
        # Initialize the trace buffer and lock
        self._traces = {}
        self._lock: RLock = RLock()
        # Track telemetry span metrics by span api
        # ex: otel api, opentracing api, datadog api
        self._spans_created = {}
        self._spans_finished = {}
        self._total_spans_finished: int = 0
        super(SpanAggregator, self).__init__()

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"{self.partial_flush_enabled}, "
            f"{self.partial_flush_min_spans}, "
            f"{self.service_name_processor},"
            f"{self.sampling_processor},"
            f"{self.tags_processor},"
            f"{self.dd_processors}, "
            f"{self.user_processors}, "
            f"{self._spans_finished}, "
            f"{self.writer})"
        )

    cdef void _emit_telemetry_metrics(self, bint force_flush = False) except *:
        ns = TELEMETRY_NAMESPACE.TRACERS
        add_count_metric = telemetry.telemetry_writer.add_count_metric

        if self._total_spans_finished >= 100 or force_flush:
            for tag_value, count in self._spans_created.items():
                add_count_metric(ns, "spans_created", count, tags=(("integration_name", tag_value),))
            self._spans_created.clear()

            for tag_value, count in self._spans_finished.items():
                add_count_metric(ns, "spans_finished", count, tags=(("integration_name", tag_value),))

            self._spans_finished.clear()
            self._total_spans_finished = 0

    cpdef void on_span_start(self, span: Span) except *:
        cdef _Trace trace
        cdef str integration_name

        with self._lock:
            trace = self._traces.setdefault(span.trace_id, _Trace())
            trace.spans.append(span)
            if config._telemetry_enabled:
                integration_name = span._meta.get(COMPONENT, span._span_api)
                self._spans_created.setdefault(integration_name, 0)
                self._spans_created[integration_name] += 1

        # perf: Avoid computed arguments unless we are actually going to log
        if log.isEnabledFor(logging.DEBUG):
            log.debug(self.SPAN_START_DEBUG_MESSAGE, span, len(trace.spans))

    cpdef void on_span_finish(self, span: Span) except *:
        cdef _Trace trace
        cdef str integration_name
        cdef int num_buffered, num_finished
        cdef bint is_trace_complete, should_partial_flush
        cdef list finished, spans

        # Acquire lock to get finished and update trace.spans
        with self._lock:
            if config._telemetry_enabled:
                self._total_spans_finished += 1
                integration_name = span._meta.get(COMPONENT, span._span_api)
                self._spans_finished.setdefault(integration_name, 0)
                self._spans_finished[integration_name] += 1
                self._emit_telemetry_metrics()

            if span.trace_id not in self._traces:
                return

            trace = self._traces[span.trace_id]
            trace.num_finished += 1
            num_buffered = len(trace.spans)
            is_trace_complete = trace.num_finished >= num_buffered
            num_finished = trace.num_finished
            should_partial_flush = False
            if is_trace_complete:
                finished = trace.spans
                del self._traces[span.trace_id]
            elif self.partial_flush_enabled and num_finished >= self.partial_flush_min_spans:
                should_partial_flush = True
                finished = trace.remove_finished()
                finished[0].set_metric("_dd.py.partial_flush", num_finished)
            else:
                return

        # perf: Process spans outside of the span aggregator lock
        spans = finished
        for tp in chain(
            self.dd_processors,
            self.user_processors,
            [self.sampling_processor, self.tags_processor, self.service_name_processor],
        ):
            try:
                spans = tp.process_trace(spans) or []
                if not spans:
                    return
            except Exception:
                log.error("error applying processor %r to trace %d", tp, span.trace_id, exc_info=True)

        if spans:
            # Get sampling information from the root span
            root_span = spans[0]._local_root
            sampling_priority = root_span.context.sampling_priority
            sampling_mechanism = root_span.context._meta.get(SAMPLING_DECISION_TRACE_TAG_KEY, "None")

            # Avoid computed arguments unless we are actually going to log
            if log.isEnabledFor(logging.DEBUG):
                log.debug(
                    self.SPAN_FINISH_DEBUG_MESSAGE,
                    len(spans),
                    num_buffered,
                    num_finished - len(spans),
                    num_buffered - num_finished,
                    spans[0].trace_id,
                    spans[0].name,
                    sampling_priority,
                    sampling_mechanism,
                    should_partial_flush,
                )
            self.writer.write(spans)

    cdef void _agent_response_callback(self, resp: AgentResponse) except *:
        """Handle the response from the agent.

        The agent can return updated sample rates for the priority sampler.
        """
        try:
            if isinstance(self.sampling_processor.sampler, DatadogSampler):
                self.sampling_processor.sampler.update_rate_by_service_sample_rates(
                    resp.rate_by_service,
                )
        except ValueError as e:
            log.error("Failed to set agent service sample rates: %s", str(e))

    cpdef void shutdown(self, timeout: Optional[float]) except *:
        """
        This will stop the background writer/worker and flush any finished traces in the buffer. The tracer cannot be
        used for tracing after this method has been called. A new tracer instance is required to continue tracing.

        :param timeout: How long in seconds to wait for the background worker to flush traces
            before exiting or :obj:`None` to block until flushing has successfully completed (default: :obj:`None`)
        :type timeout: :obj:`int` | :obj:`float` | :obj:`None`
        """
        cdef _Trace trace

        # on_span_start queue span created counts in batches of 100. This ensures all remaining counts are sent
        # before the tracer is shutdown.
        self._emit_telemetry_metrics(force_flush=True)
        # Log a warning if the tracer is shutdown before spans are finished
        if log.isEnabledFor(logging.WARNING):
            unfinished_spans = [
                f"trace_id={s.trace_id} parent_id={s.parent_id} span_id={s.span_id} name={s.name} resource={s.resource} started={s.start} sampling_priority={s.context.sampling_priority}"  # noqa: E501
                for trace in self._traces.values()
                for s in trace.spans
                if not s.finished
            ]
            if unfinished_spans:
                log.warning(
                    "Shutting down tracer with %d unfinished spans. Unfinished spans will not be sent to Datadog: %s",
                    len(unfinished_spans),
                    ", ".join(unfinished_spans),
                )

        try:
            self._traces.clear()
            self.writer.stop(timeout)
        except ServiceStatusError:
            # It's possible the writer never got started in the first place :(
            pass

    cpdef void reset(
        self,
        user_processors: Optional[List[TraceProcessor]] = None,
        compute_stats: Optional[bool] = None,
        apm_opt_out: Optional[bool] = None,
        appsec_enabled: Optional[bool] = None,
        reset_buffer: bool = True,
    ) except *:
        """
        Resets the internal state of the SpanAggregator, including the writer, sampling processor,
        user-defined processors, and optionally the trace buffer and span metrics.

        This method is typically used after a process fork or during runtime reconfiguration.
        Arguments that are None will not override existing values.
        """
        if not reset_buffer:
            # Flush any encoded spans in the writer's buffer. This operation ensures encoded spans
            # are not dropped when the writer is recreated. This operation should not be handled after a fork.
            self.writer.flush_queue()
        # Re-create the writer to ensure it is consistent with updated configurations (ex: api_version)
        self.writer = self.writer.recreate(appsec_enabled=appsec_enabled)

        if compute_stats is not None:
            self.sampling_processor._compute_stats_enabled = compute_stats

        if apm_opt_out is not None:
            self.sampling_processor.apm_opt_out = apm_opt_out

        if user_processors is not None:
            self.user_processors = user_processors

        # Reset the trace buffer and span metrics.
        # Useful when forking to prevent sending duplicate spans from parent and child processes.
        if reset_buffer:
            self._traces.clear()
            self._spans_created.clear()
            self._spans_finished.clear()
            self._total_spans_finished = 0
