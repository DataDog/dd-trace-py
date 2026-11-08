import abc
from collections import defaultdict
from itertools import chain
import logging
from typing import Optional
from typing import cast

from ddtrace._trace.context import _set_runtime_identity_generation
from ddtrace._trace.sampler import DatadogSampler
from ddtrace._trace.span import _RUNTIME_IDENTITY_GENERATION_KEY
from ddtrace._trace.span import Span
from ddtrace._trace.span import _get_64_highest_order_bits_as_hex
from ddtrace.constants import _APM_ENABLED_METRIC_KEY
from ddtrace.constants import _SINGLE_SPAN_SAMPLING_MECHANISM
from ddtrace.internal import forksafe
from ddtrace.internal import gitmetadata
from ddtrace.internal import process_tags
from ddtrace.internal._runtime_id import get_runtime_id
from ddtrace.internal._threads import periodic_threads
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.constants import HIGHER_ORDER_TRACE_ID_BITS
from ddtrace.internal.constants import LAST_DD_PARENT_ID_KEY
from ddtrace.internal.constants import MAX_UINT_64BITS
from ddtrace.internal.constants import PROCESS_TAGS
from ddtrace.internal.constants import SAMPLING_DECISION_TRACE_TAG_KEY
from ddtrace.internal.constants import SamplingMechanism
from ddtrace.internal.logger import get_logger
from ddtrace.internal.rate_limiter import RateLimiter
from ddtrace.internal.sampling import SpanSamplingRule
from ddtrace.internal.sampling import get_span_sampling_rules
from ddtrace.internal.serverless import in_aws_lambda_microvm
from ddtrace.internal.service import ServiceStatusError
from ddtrace.internal.settings._config import config
from ddtrace.internal.settings.standalone import standalone_config
from ddtrace.internal.span_processor import SpanProcessor as _SpanProcessorBase
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.internal.telemetry.metrics import MetricRecorder
from ddtrace.internal.telemetry.metrics import get_metric_recorder
from ddtrace.internal.threads import RLock
from ddtrace.internal.threads import local
from ddtrace.internal.utils.time import Time
from ddtrace.internal.writer import AgentResponse
from ddtrace.internal.writer import LogWriter
from ddtrace.internal.writer import NativeWriter
from ddtrace.internal.writer import create_trace_writer


log = get_logger(__name__)


class TraceProcessor(metaclass=abc.ABCMeta):
    def __init__(self) -> None:
        """Default post initializer which logs the representation of the
        TraceProcessor at the ``logging.DEBUG`` level.
        """
        pass

    @abc.abstractmethod
    def process_trace(self, trace: list[Span]) -> Optional[list[Span]]:
        """Processes a trace.

        ``None`` can be returned to prevent the trace from being further
        processed.
        """
        pass


class _NoopTraceProcessor(TraceProcessor):
    """Default slot occupant in ``SpanAggregator``'s chain. Lets products (LLMObs, ...)
    swap in their processor via a single attribute write instead of rebuilding the chain.
    """

    def process_trace(self, trace: list[Span]) -> Optional[list[Span]]:
        return trace


class SpanProcessor(_SpanProcessorBase[Span]):
    """A Processor is used to process spans as they are created and finished by a tracer."""


class TraceSamplingProcessor(TraceProcessor):
    """Processor that runs both trace and span sampling rules.

    * Span sampling must be applied after trace sampling priority has been set.
    * Span sampling rules are specified with a sample rate or rate limit as well as glob patterns
      for matching spans on service and name.
    * If the span sampling decision is to keep the span, then span sampling metrics are added to the span.
    * If a dropped trace includes a span that had been kept by a span sampling rule, then the span is sent to the
      Agent even if the dropped trace is not (as is the case when trace stats computation is enabled).
    * If the chunk is rejected by a rule with ``discard=True``, the whole chunk is dropped instead of being kept
      with a reject priority, so it is excluded from stats and never serialized or sent to the Agent.
    """

    def __init__(
        self,
        compute_stats_enabled: bool,
        single_span_rules: list[SpanSamplingRule],
        apm_opt_out: bool,
    ):
        super().__init__()
        self._compute_stats_enabled = compute_stats_enabled
        self.single_span_rules = single_span_rules
        self.sampler = DatadogSampler()
        self.apm_opt_out = apm_opt_out

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

    def process_trace(self, trace: list[Span]) -> Optional[list[Span]]:
        if trace:
            chunk_root = trace[0]

            if self.apm_opt_out:
                for span in trace:
                    span._set_attribute(_APM_ENABLED_METRIC_KEY, 0)

            if chunk_root.context.sampling_priority is None:
                _, discard = self.sampler.sample_or_discard(chunk_root._local_root)
                if discard:
                    return None
                if chunk_root.context.sampling_priority is None:
                    # NOTE: This should never happen, `self.sampler.sample(..)` should always set the sampling priority.
                    log.error(
                        "DatadogSampler failed to sample trace. Local Root: %s",
                        chunk_root._local_root,
                    )
                    return trace

            # single span sampling rules are applied if the trace is about to be dropped
            # DEV: the dropping is handled by the native trace exporter
            if self.single_span_rules and chunk_root.context.sampling_priority <= 0:
                for span in trace:
                    for rule in self.single_span_rules:
                        if rule.match(span):
                            # Sampling a span here does NOT effect the sampling priotiy. This operation
                            # simply marks a span as single-span sampled.
                            rule.sample(span)
                            break
            return trace
        return None


class TopLevelSpanProcessor(SpanProcessor):
    """Processor marks spans as top level

    A span is top level when it is the entrypoint method for a request to a service.
    Top level span and service entry span are equivalent terms

    The "top level" metric will be used by the agent to calculate trace metrics
    and determine how spans should be displaced in the UI. If this metric is not
    set by the tracer the first span in a trace chunk will be marked as top level.

    """

    def on_span_start(self, span: Span) -> None:
        pass

    def on_span_finish(self, span: Span) -> None:
        # DEV: Update span after finished to avoid race condition
        if span._is_top_level:
            span._set_attribute("_dd.top_level", 1)  # PERF: avoid setting via Span.set_metric


class ServiceNameProcessor(TraceProcessor):
    """Processor that adds the service name to the globalconfig."""

    def process_trace(self, trace: list[Span]) -> Optional[list[Span]]:
        for span in trace:
            if span.service:
                config._add_extra_service(span.service)
        return trace


class TraceTagsProcessor(TraceProcessor):
    """Processor that applies trace-level tags to the trace."""

    def __init__(self, apm_opt_out: bool):
        super().__init__()
        # Cached to avoid computing the opt-out property on every trace.
        self.apm_opt_out = apm_opt_out

    def _set_git_metadata(self, chunk_root):
        repository_url, commit_sha, main_package = gitmetadata.get_git_tags()
        if repository_url:
            chunk_root._set_attribute("_dd.git.repository_url", repository_url)
        if commit_sha:
            chunk_root._set_attribute("_dd.git.commit.sha", commit_sha)
        if main_package:
            chunk_root._set_attribute("_dd.python_main_package", main_package)

    def process_trace(self, trace: list[Span]) -> Optional[list[Span]]:
        if not trace:
            return trace

        spans_to_tag = [trace[0]]

        # When using the native writer and CSS, TraceTagsProcessor runs before dropping spans.
        # Thus trace tags are applied to a root span which may be dropped by sampling, even though
        # some spans of the chunk are sampled. We prevent it by adding trace tags to the first
        # single-sampled span of the chunk. Standalone opt-out shadows stats computation, so
        # single-sampled spans do not outlive their root and must not receive the tags.
        if config._trace_compute_stats and not self.apm_opt_out:
            for span in trace:
                if span.get_metric(_SINGLE_SPAN_SAMPLING_MECHANISM) == SamplingMechanism.SPAN_SAMPLING_RULE:
                    spans_to_tag.append(span)
                    break

        for span in spans_to_tag:
            span._update_tags_from_context()
            self._set_git_metadata(span)
            if not config._otel_trace_semantics_enabled:
                span._set_attribute("language", "python")
            if p_tags := process_tags.process_tags:
                span._set_attribute(PROCESS_TAGS, p_tags)
            # for 128 bit trace ids
            # PERF: cache trace_id to avoid repeated Rust property calls (each call allocates a new Python int)
            trace_id = span.trace_id
            if trace_id > MAX_UINT_64BITS:
                trace_id_hob = _get_64_highest_order_bits_as_hex(trace_id)
                span._set_attribute(HIGHER_ORDER_TRACE_ID_BITS, trace_id_hob)

            if span._has_attribute(LAST_DD_PARENT_ID_KEY) and span._parent is not None:
                # we should only set the last parent id on local root spans
                span._remove_attribute(LAST_DD_PARENT_ID_KEY)

        return trace


class _Trace:
    __slots__ = ("spans", "finished_ids", "discarded")

    def __init__(self, spans: Optional[list[Span]] = None):
        self.spans: list[Span] = spans if spans is not None else []
        # id() of spans already passed to SpanAggregator.on_span_finish. A span's duration_ns is set before
        # its finish callbacks run, so another thread can see it as finished before it is counted.
        # id() rather than span_id: it avoids a native getter call and does not assume span ids are unique
        # within a trace. Buffered spans are alive, so their id() cannot be reused while in the set.
        self.finished_ids: set[int] = set()
        self.discarded: bool = False

    def remove_finished(self) -> list[Span]:
        finished_ids = self.finished_ids
        finished: list[Span] = []
        remaining: list[Span] = []
        for s in self.spans:
            if id(s) in finished_ids:
                finished.append(s)
            else:
                remaining.append(s)
        self.spans[:] = remaining
        # Also drops ids of spans that were never buffered here, so they stop inflating the count.
        finished_ids.clear()
        return finished


def _resolve_apm_trace_agentless() -> bool:
    """Whether the APM trace writer should run in agentless mode.

    Falls back (returns False with a warning) when agentless is requested but ``DD_API_KEY``
    is unset.
    """
    if not config._trace_agentless_enabled:
        return False
    if not config._dd_api_key:
        log.warning("APM Agentless enabled but DD_API_KEY is not set. Agentless mode will be disabled.")
        return False
    return True


# Track telemetry span metrics by span api
# ex: otel api, datadog api
_SPANS_CREATED_RECORDERS: dict[str, MetricRecorder] = {}
_SPANS_FINISHED_RECORDERS: dict[str, MetricRecorder] = {}
# spans_dropped has a single reason so far, so it needs no per-value map.
_SPANS_DROPPED_TRACE_PROCESSOR = get_metric_recorder(
    TELEMETRY_NAMESPACE.TRACERS, "spans_dropped", tags=(("reason", "trace_processor"),)
)


class _RetiredWriter:
    def __init__(self, writer: NativeWriter) -> None:
        self.writer = writer
        self._lock = forksafe.RLock()
        self._finished = False


class SpanAggregator(SpanProcessor):
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

    def __init__(
        self,
        partial_flush_enabled: bool,
        partial_flush_min_spans: int,
        dd_processors: Optional[list[TraceProcessor]] = None,
        user_processors: Optional[list[TraceProcessor]] = None,
    ):
        # Set partial flushing
        self.partial_flush_enabled = partial_flush_enabled
        self.partial_flush_min_spans = partial_flush_min_spans
        # Initialize trace processors
        self.sampling_processor = TraceSamplingProcessor(
            config._trace_compute_stats, get_span_sampling_rules(), standalone_config.apm_opt_out
        )
        self.tags_processor = TraceTagsProcessor(standalone_config.apm_opt_out)
        self.dd_processors = dd_processors or []
        self.user_processors = user_processors or []
        self.service_name_processor = ServiceNameProcessor()
        self.llmobs_processor: TraceProcessor = _NoopTraceProcessor()
        self.writer = create_trace_writer(
            response_callback=self._agent_response_callback,
            agentless=_resolve_apm_trace_agentless(),
        )
        # MicroVM only: (generation, runtime ID the writer was built for). A refresh publishes both as
        # one value, so span start and finish read them without taking a lock.
        self._runtime_identity: Optional[tuple[int, str]] = (0, get_runtime_id()) if in_aws_lambda_microvm() else None
        self._traces: defaultdict[int, _Trace] = defaultdict(lambda: _Trace())
        # Only MicroVM identity refreshes need a lock that resets after fork; keep the plain lock elsewhere.
        self._lock = forksafe.RLock() if in_aws_lambda_microvm() else RLock()
        # A sync-mode writer whose send was deferred until the finishing thread releases its locks.
        self._pending_sync_flush = local()
        # Keep detached writers visible until their services have been stopped.
        self._retired_writers: dict[NativeWriter, _RetiredWriter] = {}
        super().__init__()

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
            f"{self.writer})"
        )

    @property
    def _identity_refresh_enabled(self) -> bool:
        """Whether this aggregator was initialized for MicroVM identity refresh."""
        return self._runtime_identity is not None

    @property
    def _runtime_identity_generation(self) -> Optional[int]:
        identity = self._runtime_identity
        return identity[0] if identity is not None else None

    # The checks below read the published identity without the lock. A stale read can only drop a
    # trace: _write_if_identity_generation_is_current() re-checks under the lock before writing.
    def _identity_generation_is_current(self, identity_generation: int) -> bool:
        identity = self._runtime_identity
        return identity is not None and identity_generation == identity[0]

    def _is_span_identity_current(self, span: Span) -> bool:
        """Return whether span belongs to the current MicroVM identity generation."""
        identity = self._runtime_identity
        if identity is None:
            return True
        return span._get_ctx_item(_RUNTIME_IDENTITY_GENERATION_KEY) == identity[0]

    # Keep the generation check and write atomic so refresh cannot invalidate the trace between them.
    def _write_if_identity_generation_is_current(self, identity_generation: int, spans: list[Span]) -> None:
        with self._lock:
            if self._identity_generation_is_current(identity_generation):
                writer = self.writer
                # A sync NativeWriter's write() sends on this thread, which would hold this lock across
                # send() retries. Buffer under the lock instead and
                # leave the send to _flush_pending_sync_write(). Async writers, and writers without
                # _write_without_flush (LogWriter, HTTPWriter), keep using write().
                write_without_flush = getattr(writer, "_write_without_flush", None)
                if write_without_flush is None or not getattr(writer, "_sync_mode", False):
                    writer.write(spans)
                elif write_without_flush(spans):
                    self._pending_sync_flush.writer = writer

    def _flush_pending_sync_write(self) -> None:
        # Called at the end of Tracer._on_span_finish, after this aggregator's lock is released. The slot is
        # thread-local so each finishing thread sends only its own write, and it is cleared first so a
        # failed send is not retried by the next span finish.
        writer = getattr(self._pending_sync_flush, "writer", None)
        if writer is not None:
            self._pending_sync_flush.writer = None
            writer.flush_queue()

    def on_span_start(self, span: Span) -> None:
        # PERF: cache trace_id to avoid repeated Rust property calls (each call allocates a new Python int)
        trace_id = span.trace_id
        with self._lock:
            # Direct processor callers may not go through Tracer.start_span; stamp those spans here,
            # while rejecting spans captured before an identity refresh.
            identity = self._runtime_identity
            if identity is not None:
                span_generation = span._get_ctx_item(_RUNTIME_IDENTITY_GENERATION_KEY)
                if span_generation is None:
                    span_generation = identity[0]
                    span._set_ctx_item(_RUNTIME_IDENTITY_GENERATION_KEY, span_generation)
                # Record before rejecting so a retained context of a rejected span stays stale.
                _set_runtime_identity_generation(span.context, span_generation)
                if span_generation != identity[0]:
                    return
            trace = self._traces[trace_id]
            trace.spans.append(span)
        integration_name = span._get_str_attribute(COMPONENT) or span._span_api
        recorder = _SPANS_CREATED_RECORDERS.get(integration_name)
        if recorder is None:
            recorder = _SPANS_CREATED_RECORDERS[integration_name] = get_metric_recorder(
                TELEMETRY_NAMESPACE.TRACERS, "spans_created", tags=(("integration_name", integration_name),)
            )
        recorder.add()
        log.debug(self.SPAN_START_DEBUG_MESSAGE, span, len(trace.spans))

    def on_span_finish(self, span: Span) -> None:
        # PERF: cache trace_id to avoid repeated Rust property calls (each call allocates a new Python int)
        trace_id = span.trace_id
        integration_name = span._get_str_attribute(COMPONENT) or span._span_api
        recorder = _SPANS_FINISHED_RECORDERS.get(integration_name)
        if recorder is None:
            recorder = _SPANS_FINISHED_RECORDERS[integration_name] = get_metric_recorder(
                TELEMETRY_NAMESPACE.TRACERS, "spans_finished", tags=(("integration_name", integration_name),)
            )
        recorder.add()
        # Acquire lock to get finished and update trace.spans
        with self._lock:
            if trace_id not in self._traces:
                return

            trace = self._traces[trace_id]
            identity = self._runtime_identity
            identity_generation = identity[0] if identity is not None else None
            if (
                identity_generation is not None
                and span._get_ctx_item(_RUNTIME_IDENTITY_GENERATION_KEY) != identity_generation
            ):
                return
            trace.finished_ids.add(id(span))
            num_buffered = len(trace.spans)
            num_finished = len(trace.finished_ids)
            is_trace_complete = num_finished >= num_buffered
            should_partial_flush = False
            if is_trace_complete:
                finished = trace.spans
                del self._traces[trace_id]
            elif self.partial_flush_enabled and num_finished >= self.partial_flush_min_spans:
                should_partial_flush = True
                finished = trace.remove_finished()
                if finished:
                    finished[0]._set_attribute("_dd.py.partial_flush", num_finished)
                else:
                    # finished_ids only held spans that were never buffered in this trace (for example,
                    # started before the aggregator was reset), so there is nothing to flush.
                    return
            else:
                return
            # Capture the generation before processing so an identity refresh can invalidate this trace.
            if identity_generation is not None and any(
                finished_span._get_ctx_item(_RUNTIME_IDENTITY_GENERATION_KEY) != identity_generation
                for finished_span in finished
            ):
                return

        # The aggregation lock is released before processor execution; recheck the generation
        # so a refresh that wins this gap cannot send the stale trace through the chain.
        if identity_generation is not None and not self._identity_generation_is_current(identity_generation):
            return

        # perf: Process spans outside of the span aggregator lock
        if trace.discarded:
            spans: list[Span] = []
        else:
            spans = finished
            sampling_discarded = False
            for tp in chain(
                self.dd_processors,
                self.user_processors,
                [
                    self.sampling_processor,
                    self.llmobs_processor,
                    self.tags_processor,
                    self.service_name_processor,
                ],
            ):
                if identity_generation is not None and not self._identity_generation_is_current(identity_generation):
                    return
                try:
                    result = tp.process_trace(spans) or []
                except Exception:
                    log.error("error applying processor %r to trace %d", tp, span.trace_id, exc_info=True)
                    continue
                if not result and tp is self.sampling_processor:
                    # TraceSamplingProcessor only ever returns an empty result for a discard=True
                    # rule rejecting the chunk. However, we must not actually discard them until the
                    # llmobs_processor has run to allow them to rescue spans.
                    sampling_discarded = True
                    if not is_trace_complete:
                        # Remember this so later partial-flush chunks of the same trace are dropped
                        # too, instead of being kept with the (already-set) reject priority.
                        trace.discarded = True
                    continue
                spans = result
                if not spans or (tp is self.llmobs_processor and sampling_discarded):
                    # If llmobs does not rescue the span, we still discard it.
                    spans = []
                    break

        num_dropped = len(finished) - len(spans)
        if num_dropped > 0:
            _SPANS_DROPPED_TRACE_PROCESSOR.add(num_dropped)

        if spans:
            # Get sampling information from the root span
            root_span = spans[0]._local_root
            sampling_priority = root_span.context.sampling_priority
            sampling_mechanism = root_span.context._meta.get(SAMPLING_DECISION_TRACE_TAG_KEY, "None")

            if log.isEnabledFor(logging.DEBUG):
                log.debug(
                    self.SPAN_FINISH_DEBUG_MESSAGE,
                    len(spans),
                    num_buffered,
                    num_dropped,
                    num_buffered - num_finished,
                    spans[0].trace_id,
                    spans[0].name,
                    sampling_priority,
                    sampling_mechanism,
                    should_partial_flush,
                )
            if identity_generation is None:
                self.writer.write(spans)
            else:
                self._write_if_identity_generation_is_current(identity_generation, spans)

    def _agent_response_callback(self, resp: AgentResponse) -> None:
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

    def shutdown(self, timeout: Optional[float]) -> None:
        """
        This will stop the background writer/worker and flush any finished traces in the buffer. The tracer cannot be
        used for tracing after this method has been called. A new tracer instance is required to continue tracing.

        :param timeout: How long in seconds to wait for the background worker to flush traces
            before exiting or :obj:`None` to block until flushing has successfully completed (default: :obj:`None`)
        :type timeout: :obj:`int` | :obj:`float` | :obj:`None`
        """
        # Log a warning if the tracer is shutdown before spans are finished
        if log.isEnabledFor(logging.WARNING):
            unsent_spans = [
                f"trace_id={s.trace_id} parent_id={s.parent_id} span_id={s.span_id} name={s.name} resource={s.resource} started={s.start} sampling_priority={s.context.sampling_priority}"  # noqa: E501
                for t in self._traces.values()
                for s in t.spans
            ]
            if unsent_spans:
                log.warning(
                    "Shutting down tracer with %d spans. These spans will not be sent to Datadog: %s",
                    len(unsent_spans),
                    ", ".join(unsent_spans),
                )

        retirement_errors: list[Exception] = []
        if self._identity_refresh_enabled:
            # Wait outside the identity lock, sharing one budget with the active writer.
            deadline = None if timeout is None else Time.monotonic() + max(timeout, 0)
            # Copy the dictionary without waiting on the identity lock: a refresh may hold
            # it while waiting for a writer lock owned by the interrupted signal-handler thread.
            retired_writers = self._retired_writers.copy()
            for retired_writer in retired_writers.values():
                remaining = None if deadline is None else max(deadline - Time.monotonic(), 0)
                try:
                    self._finish_retired_writer(retired_writer, flush=False, timeout=remaining)
                except Exception as error:
                    retirement_errors.append(error)
            # Share the remaining budget with the active writer.
            timeout = None if deadline is None else max(deadline - Time.monotonic(), 0)

        try:
            self._traces.clear()
            self.writer.stop(timeout)
        except ServiceStatusError:
            # It's possible the writer never got started in the first place :(
            pass
        finally:
            for retirement_error in retirement_errors:
                log.warning(
                    "Failed to stop retired trace writer during shutdown",
                    exc_info=(type(retirement_error), retirement_error, retirement_error.__traceback__),
                )

    def configure_agentless_writer(self, enable: bool) -> bool:
        """
        Swap the writer between intake and agent submission. Returns True if a swap occurred.
        """
        if self._identity_refresh_enabled:
            with self._lock:
                return self._configure_agentless_writer(enable)
        return self._configure_agentless_writer(enable)

    def _configure_agentless_writer(self, enable: bool) -> bool:
        if isinstance(self.writer, LogWriter):
            # perf: LogWriter is chosen by create_trace_writer regardless of agentless configs; skip the swap early.
            return False
        if getattr(self.writer, "agentless", False) is enable:
            return False

        old_writer = self.writer
        try:
            self.writer = create_trace_writer(response_callback=self._agent_response_callback, agentless=enable)
        except Exception:
            log.error(
                "Failed to create %s APM trace writer; writer swap aborted.",
                "agentless" if enable else "agent-based",
                exc_info=True,
            )
            return False
        try:
            old_writer.flush_queue()
            old_writer.stop()
        except ServiceStatusError:
            # The writer never started, so there is no periodic thread to stop
            # But the native writer builds its exporter in __init__, so we free it
            shutdown_exporter = getattr(old_writer, "shutdown_exporter", None)
            if shutdown_exporter is not None:
                shutdown_exporter()
        except Exception:
            log.warning(
                "Failed to flush and stop previous APM trace writer while configuring agentless writer", exc_info=True
            )
        return True

    def _finish_retired_writer(
        self, retired_writer: _RetiredWriter, flush: bool, timeout: Optional[float] = None
    ) -> None:
        # A signal handler can interrupt this thread while it owns the retirement. Skip
        # self-waits so the active writer retains its shutdown budget.
        if retired_writer._lock._is_owned():  # type: ignore[attr-defined]
            return
        # A signal can interrupt a manual flush while another thread owns the retirement and
        # waits for this exporter lock. Leave cleanup pending so the flush can resume.
        if retired_writer.writer._exporter_lock._is_owned():  # type: ignore[attr-defined]
            return
        # Coordinate shutdown with the configuring thread without holding the identity lock across I/O.
        deadline = None if timeout is None else Time.monotonic() + max(timeout, 0)
        if not retired_writer._lock.acquire(timeout=-1 if timeout is None else max(timeout, 0)):
            return
        try:
            if retired_writer._finished:
                return
            writer = retired_writer.writer
            try:
                if flush:
                    writer.flush_queue()
            finally:
                remaining = None if deadline is None else max(deadline - Time.monotonic(), 0)
                try:
                    writer.stop(remaining)
                except ServiceStatusError:
                    # A stopped service may still be finishing a timed-out join.
                    if writer._worker is not None:
                        writer.join(remaining)
                    elif remaining is None:
                        # A writer that never received a trace has no periodic thread to stop.
                        # Recheck the discard gate before choosing native shutdown or drop.
                        writer.shutdown_exporter()
                    else:
                        writer.shutdown_exporter(timeout=min(remaining, 3.0))
                # A timed-out join can return while the worker still runs on_shutdown(). The worker
                # leaves periodic_threads only after that final flush, so keep the writer visible
                # to refresh until then.
                worker = writer._worker
                if worker is None or periodic_threads.get(worker.ident) is not worker:
                    # Keep failures visible for shutdown or refresh to retry. Mark success before
                    # releasing the retirement lock so a waiting shutdown cannot stop it twice.
                    retired_writer._finished = True
                    self._retired_writers.pop(writer, None)
        finally:
            retired_writer._lock.release()

    def reset(
        self,
        user_processors: Optional[list[TraceProcessor]] = None,
        compute_stats: Optional[bool] = None,
        apm_opt_out: Optional[bool] = None,
        appsec_enabled: Optional[bool] = None,
        llmobs_enabled: Optional[bool] = None,
        reset_buffer: bool = True,
        flush_writer: Optional[bool] = None,
        drop_buffered_traces: bool = False,
    ) -> None:
        """
        Resets the internal state of the SpanAggregator, including the writer, sampling processor,
        user-defined processors, and optionally the trace buffer and span metrics.

        This method is typically used after a process fork or during runtime reconfiguration.
        Arguments that are None will not override existing values. By default, the writer is
        flushed only when preserving the trace buffer.
        """
        if flush_writer is None:
            flush_writer = not reset_buffer

        retired_writer: Optional[_RetiredWriter] = None
        if self._identity_refresh_enabled:
            # Only construction/publication needs the identity lock; teardown can wait on I/O.
            with self._lock:
                retired_writer = self._reset_writer(
                    appsec_enabled,
                    llmobs_enabled,
                    compute_stats,
                    apm_opt_out,
                    reset_buffer,
                    flush_writer,
                    drop_buffered_traces,
                )
        else:
            self._reset_writer(
                appsec_enabled,
                llmobs_enabled,
                compute_stats,
                apm_opt_out,
                reset_buffer,
                flush_writer,
                drop_buffered_traces,
            )

        if compute_stats is not None:
            self.sampling_processor._compute_stats_enabled = compute_stats

        if apm_opt_out is not None:
            self.sampling_processor.apm_opt_out = apm_opt_out
            self.tags_processor.apm_opt_out = apm_opt_out

        if user_processors is not None:
            self.user_processors = user_processors

        if retired_writer is not None:
            try:
                self._finish_retired_writer(retired_writer, flush_writer)
            except Exception:
                log.warning("Failed to flush and stop retired trace writer during reconfiguration", exc_info=True)

    def _reset_writer(
        self,
        appsec_enabled: Optional[bool],
        llmobs_enabled: Optional[bool],
        compute_stats: Optional[bool],
        apm_opt_out: Optional[bool],
        reset_buffer: bool,
        flush_writer: bool,
        drop_buffered_traces: bool,
    ) -> Optional[_RetiredWriter]:
        # Only explicit MicroVM refreshes use discard-and-recreate semantics.
        if drop_buffered_traces and not flush_writer and self._identity_refresh_enabled:
            # The MicroVM-only branch guarantees that the identity is initialized. The refresh callback
            # runs after the runtime ID rotated, so get_runtime_id() is the ID the new writer is built for.
            generation = cast(tuple[int, str], self._runtime_identity)[0] + 1
            self._runtime_identity = (generation, get_runtime_id())
            if reset_buffer:
                self.reset_trace_buffer_after_fork()
            # Dropping an exporter does not stop its Python worker. Keep failed retirements
            # reachable so shutdown can stop them; an ongoing drain removes its own entry.
            for writer in self._retired_writers.copy():
                writer.drop_buffered_traces()
            self.writer.drop_buffered_traces()
            self.writer = self.writer.recreate(
                appsec_enabled=appsec_enabled,
                llmobs_enabled=llmobs_enabled,
                compute_stats_enabled=compute_stats,
                stats_opt_out=apm_opt_out,
            )
            return None

        if self._identity_refresh_enabled and flush_writer and isinstance(self.writer, NativeWriter):
            old_writer = self.writer
            # Construction can fail: leave the installed writer and its gates intact in that case.
            replacement = old_writer._recreate(appsec_enabled, llmobs_enabled, compute_stats, apm_opt_out)
            retired_writer = _RetiredWriter(old_writer)
            self._retired_writers[old_writer] = retired_writer
            self.writer = replacement
            self._runtime_identity = (cast(tuple[int, str], self._runtime_identity)[0], get_runtime_id())
            if reset_buffer:
                self.reset_trace_buffer_after_fork()
            return retired_writer

        if flush_writer:
            # Flush any encoded spans in the writer's buffer. This operation ensures encoded spans
            # are not dropped when the writer is recreated. This operation should not be handled after a fork.
            self.writer.flush_queue()
        elif drop_buffered_traces:
            self.writer.drop_buffered_traces()
        # Re-create the writer to ensure it is consistent with updated configurations (ex: api_version)
        self.writer = self.writer.recreate(
            appsec_enabled=appsec_enabled,
            llmobs_enabled=llmobs_enabled,
            compute_stats_enabled=compute_stats,
            stats_opt_out=apm_opt_out,
        )
        if self._runtime_identity is not None:
            # A writer rebuilt after a fork is built for the child's runtime ID.
            self._runtime_identity = (self._runtime_identity[0], get_runtime_id())

        # Reset the trace buffer.
        # Useful when forking to prevent sending duplicate spans from parent and child processes.
        if reset_buffer:
            self.reset_trace_buffer_after_fork()
        return None

    def reset_trace_buffer_after_fork(self) -> None:
        """Discard inherited traces without touching the fork-unsafe writer."""
        self._traces = defaultdict(lambda: _Trace())
