"""
Feature Flagging and Experimentation (FFE) product module.

This module handles Feature Flag configuration rules from Remote Configuration
and forwards the raw bytes to the native FFE processor.
"""

from collections import OrderedDict
from collections.abc import MutableMapping
from contextlib import contextmanager
import time
import typing
import weakref


if typing.TYPE_CHECKING:
    from ddtrace.internal.openfeature._agentless_source import AgentlessConfigurationSource

from openfeature.evaluation_context import EvaluationContext
from openfeature.event import ProviderEventDetails
from openfeature.exception import ErrorCode
from openfeature.exception import ProviderNotReadyError
from openfeature.flag_evaluation import FlagEvaluationDetails
from openfeature.flag_evaluation import FlagResolutionDetails
from openfeature.flag_evaluation import FlagValueType
from openfeature.flag_evaluation import Reason
from openfeature.hook import Hook
from openfeature.hook import HookContext
from openfeature.hook import HookHints
from openfeature.provider import Metadata
from openfeature.provider import ProviderStatus

from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native._native import ffe
from ddtrace.internal.openfeature._config import _FfeSnapshot
from ddtrace.internal.openfeature._config import _get_ffe_snapshot
from ddtrace.internal.openfeature._config import _register_provider
from ddtrace.internal.openfeature._config import _unregister_provider
from ddtrace.internal.openfeature._debug_logging_hook import DebugLoggingHook
from ddtrace.internal.openfeature._debug_logging_hook import debug_logging_enabled
from ddtrace.internal.openfeature._debug_logging_hook import log_provider_resolution
from ddtrace.internal.openfeature._debug_logging_hook import warn_raw_evaluation_data_logged
from ddtrace.internal.openfeature._evp_transport import reset_feature_flag_evp_route_selector
from ddtrace.internal.openfeature._exposure import build_exposure_event
from ddtrace.internal.openfeature._exposure_decision import METADATA_EXPOSURE_CACHE_HIT
from ddtrace.internal.openfeature._exposure_decision import ExposureCacheKey
from ddtrace.internal.openfeature._exposure_decision import ExposureCacheValue
from ddtrace.internal.openfeature._exposure_decision import ExposureDecision
from ddtrace.internal.openfeature._exposure_decision import FlagMetadata
from ddtrace.internal.openfeature._exposure_decision import complete_exposure_decision
from ddtrace.internal.openfeature._flag_eval_evp_hook import FlagEvalEVPHook
from ddtrace.internal.openfeature._flag_selection import FlagSelectionValues
from ddtrace.internal.openfeature._flag_selection import canonicalize_flag_selection
from ddtrace.internal.openfeature._flag_selection import recount_process_selection_in_child
from ddtrace.internal.openfeature._flag_selection import release_process_selection
from ddtrace.internal.openfeature._flag_selection import snapshot_from_other_selection
from ddtrace.internal.openfeature._flageval_metrics import METADATA_ALLOCATION_KEY
from ddtrace.internal.openfeature._flageval_metrics import FlagEvalMetrics
from ddtrace.internal.openfeature._flageval_metrics import FlagEvalMetricsHook
from ddtrace.internal.openfeature._flagevaluation_writer import DRAIN_WORKER_JOIN_TIMEOUT
from ddtrace.internal.openfeature._flagevaluation_writer import EVAL_TIMESTAMP_METADATA_KEY
from ddtrace.internal.openfeature._flagevaluation_writer import METADATA_OBSERVE_FULL_EVALUATION_DATA
from ddtrace.internal.openfeature._flagevaluation_writer import FlagEvaluationWriter
from ddtrace.internal.openfeature._native import VariationType
from ddtrace.internal.openfeature._native import _native_attributes
from ddtrace.internal.openfeature._native import resolve_flag
from ddtrace.internal.openfeature._span_enrichment import METADATA_DO_LOG
from ddtrace.internal.openfeature._span_enrichment import METADATA_SERIAL_ID
from ddtrace.internal.openfeature._span_enrichment import SpanEnrichmentHook
from ddtrace.internal.openfeature._split_features import METADATA_EXPOSURE_FEATURE_PREFIX
from ddtrace.internal.openfeature._split_features import add_split_features
from ddtrace.internal.openfeature._split_features import features_with_prefix
from ddtrace.internal.openfeature.writer import get_exposure_writer
from ddtrace.internal.openfeature.writer import start_exposure_writer
from ddtrace.internal.openfeature.writer import stop_exposure_writer
from ddtrace.internal.service import ServiceStatusError
from ddtrace.internal.settings.openfeature import OpenFeatureConfig
from ddtrace.internal.settings.openfeature import config as ffe_config
from ddtrace.internal.threads import Event
from ddtrace.internal.threads import Lock
from ddtrace.internal.threads import current_thread


# Handle different import paths between openfeature-sdk versions.
# Versions 0.7.0+ reorganized submodules (AbstractProvider moved to
# openfeature.provider). A string comparison of the version is unsafe -- e.g.
# "0.10.0" >= "0.7.0" is False lexicographically -- so resolve by import
# instead, which is correct for any version-string format.
try:
    from openfeature.provider import AbstractProvider
except ImportError:
    from openfeature.provider.provider import AbstractProvider


T = typing.TypeVar("T", covariant=True)
ResolvedValue = typing.TypeVar("ResolvedValue")
ObjectFlagValue = typing.Union[typing.Sequence[FlagValueType], typing.Mapping[str, FlagValueType]]
K = typing.TypeVar("K")
V = typing.TypeVar("V")
logger = get_logger(__name__)


class LRUCache(MutableMapping, typing.Generic[K, V]):
    """LRU cache implementation using OrderedDict that implements the Mapping interface."""

    def __init__(self, maxsize: int = 128):
        self._cache: typing.OrderedDict[K, V] = OrderedDict()
        self._maxsize = maxsize

    def __getitem__(self, key: K) -> V:
        """Get value from cache, moving it to end (most recently used)."""
        self._cache.move_to_end(key)
        return self._cache[key]

    def __setitem__(self, key: K, value: V) -> None:
        """Put value in cache, evicting least recently used if at capacity."""
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = value
        if len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)  # Remove least recently used (first item)

    def __delitem__(self, key: K) -> None:
        """Delete key from cache."""
        del self._cache[key]

    def __iter__(self) -> typing.Iterator[K]:
        """Iterate over cache keys."""
        return iter(self._cache)

    def __len__(self) -> int:
        """Return number of items in cache."""
        return len(self._cache)

    def clear(self) -> None:
        # MutableMapping.clear() iterates, which fails while an unlocked reader reorders entries.
        self._cache.clear()


class DataDogProvider(AbstractProvider):
    """
    Datadog OpenFeature Provider.

    Implements the OpenFeature provider interface for Datadog's
    Feature Flags and Experimentation (FFE) product.

    flag_selection limits the flags loaded from the agentless configuration
    endpoint, for example
    DataDogProvider(flag_selection={"tags": ["checkout"], "key_patterns": ["checkout.*"]}).
    A flag is loaded when it has any of the tags or its whole key matches any of
    the key patterns, where "*" matches any run of characters, including none,
    and every other character, such as ".", is literal. Without a selection
    every flag is loaded. Other keys, including keyPatterns, are ignored. Each
    list accepts at most 31 strings of at most 200 UTF-8 bytes, and the request
    URL with the selection must fit in 8192 bytes; a selection that breaks a
    limit is logged and every flag is loaded. A process uses one selection: the
    first provider to initialize sets it until every provider has shut down. A
    provider set while another one is still running uses the running one's
    selection, so to change it call openfeature.api.shutdown() before setting
    the new provider. Remote Config delivery ignores the selection. LLM
    Observability evaluates prompt flags through its own provider, which shares
    the process selection: prompt flags outside the selection in use are not
    loaded, and if that provider starts first, this provider's selection is not
    sent.
    """

    def __init__(
        self,
        *args: typing.Any,
        initialization_timeout: typing.Optional[float] = None,
        flag_selection: typing.Optional[typing.Mapping[str, typing.Optional[FlagSelectionValues]]] = None,
        **kwargs: typing.Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._metadata = Metadata(name="Datadog")
        self._status = ProviderStatus.NOT_READY
        # Canonicalized now so later changes to the caller's lists have no effect.
        self._flag_selection = canonicalize_flag_selection(flag_selection)

        # Event set when the first config arrives; used by on_configuration_received()
        # to guard the first-config path and by _emit_ready_event() timing.
        self._config_received = Event()
        self._initialization_timed_out = False

        # Read through a fresh config instance so values reflect the environment at
        # provider-construction time rather than at import time (see the killswitch
        # note below for the same reasoning).
        instance_config = OpenFeatureConfig()

        # How long initialize() waits for that first config, in seconds. The
        # constructor argument wins so embedders and tests can override the
        # environment; otherwise the configured value applies.
        if initialization_timeout is None:
            initialization_timeout = instance_config.initialization_timeout_ms / 1000.0
        self._initialization_timeout = initialization_timeout

        # Cache for reported exposures to prevent duplicates
        # Stores mapping of (flag_key, subject_id) -> (allocation_key, variant_key, serial_id)
        # The serial id is part of the value because a configuration refresh can add or
        # change it while the allocation and variant stay the same, and that must be sent.
        # Using LRU cache with maxsize of 65536 to prevent unbounded memory growth
        self._exposure_cache: LRUCache[ExposureCacheKey, ExposureCacheValue] = LRUCache(maxsize=65536)
        self._exposure_cache_lock = Lock()
        _providers_with_exposure_cache.add(self)
        self._exposure_logging_hook = ExposureLoggingHook()

        # Master gate: the resolved configuration source (stable kill switch +
        # source selection, with legacy grandfathering). Mirrors dd-trace-js,
        # where the provider only activates when a delivery source is selected.
        from ddtrace.internal.openfeature._source_selection import DISABLED
        from ddtrace.internal.openfeature._source_selection import resolve_configuration_source

        self._active = resolve_configuration_source(ffe_config) != DISABLED
        if not self._active:
            logger.warning(
                "openfeature: Feature Flagging provider is disabled; set DD_FEATURE_FLAGS_ENABLED=true and "
                "DD_FEATURE_FLAGS_CONFIGURATION_SOURCE to a supported value to enable it",
            )

        # NOTE: the legacy DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED flag is an input
        # to source grandfathering (handled during resolution above) only. Evaluation,
        # hooks and telemetry all key off the resolved decision (``_active``), matching
        # dd-trace-js where the same switch gates the provider and its writers.

        # Agentless configuration-source poller; started in initialize() when agentless is the resolved
        # source and stopped in shutdown(). The lock only guards these fields, never logging or callbacks:
        # a start and a teardown each reserve the source under it and do their work outside it.
        self._configuration_source: typing.Optional[AgentlessConfigurationSource] = None
        self._configuration_source_lock = forksafe.Lock()
        self._starting_configuration_source = False
        # The thread of the start in progress, and the shutdown count when it began.
        self._configuration_source_starter: typing.Any = None
        # Set, and replaced, when a start ends or a shutdown happens, so waiting starts check again.
        self._configuration_source_changed = Event()
        # Counts shutdowns, so a start or a waiting start can tell that one happened after it began.
        self._configuration_source_shutdowns = 0
        self._stopping_configuration_source = False
        self._configuration_source_stopper: typing.Any = None
        # Pollers whose teardown failed. Each keeps its claim until a later shutdown finishes it.
        self._retired_configuration_sources: list[AgentlessConfigurationSource] = []
        # A source a start built and has not stored or discarded; it holds a claim.
        self._pending_configuration_source: typing.Optional[AgentlessConfigurationSource] = None
        # The thread of each start or teardown in progress, from before it takes a claim until it has stored or
        # released it. Guarded by _lifecycle_lock; a fork waits for these (_wait_for_lifecycle_before_fork).
        self._configuration_source_operations: list[typing.Any] = []

        # Initialize flag evaluation metrics tracking
        # Metrics are emitted via OTel when DD_METRICS_OTEL_ENABLED=true
        self._flag_eval_metrics: typing.Optional[FlagEvalMetrics] = None
        self._flag_eval_metrics_hook: typing.Optional[FlagEvalMetricsHook] = None
        if self._active:
            self._flag_eval_metrics = FlagEvalMetrics()
            self._flag_eval_metrics_hook = FlagEvalMetricsHook(self._flag_eval_metrics)

        # EVP flagevaluation writer + hook — gated by DD_FLAGGING_EVALUATION_COUNTS_ENABLED
        # (default on). Gates ONLY the EVP path; the OTel path above is always registered
        # when the provider is enabled (preserves the existing OTel non-regression).
        # the killswitch is read through the ddtrace config system
        # (OpenFeatureConfig.flagging_evaluation_counts_enabled, registered in
        # supported-configurations.json) rather than raw os.environ. It comes from the
        # fresh instance_config built at the top of __init__, so the value reflects the
        # environment at provider-construction time (the config var parses the live
        # environment via the DDConfig var system), which keeps the killswitch overridable
        # per-instance in tests.
        self._flag_eval_evp_writer: typing.Optional[FlagEvaluationWriter] = None
        self._flag_eval_evp_hook: typing.Optional[FlagEvalEVPHook] = None
        evp_counts_enabled = instance_config.flagging_evaluation_counts_enabled
        if self._active and evp_counts_enabled:
            self._flag_eval_evp_writer = FlagEvaluationWriter()
            self._flag_eval_evp_hook = FlagEvalEVPHook(self._flag_eval_evp_writer)

        # APM span enrichment hook (experimental, distinct gate, OFF by default).
        # Constructed ONLY when the gate is on, so nothing is allocated and
        # nothing subscribes to span finish when it is off (DG-005).
        self._span_enrichment_hook: typing.Optional[SpanEnrichmentHook] = None
        if self._active and ffe_config.experimental_flagging_provider_span_enrichment_enabled:
            self._span_enrichment_hook = SpanEnrichmentHook()

        # Evaluation-detail logging for self-diagnosis. Registered only when debug
        # logs would actually be emitted, so it costs nothing otherwise. The
        # provider-resolution line is gated on this hook too, so enabling debug after
        # creation never starts the evaluation-detail lines; warning here covers them.
        self._debug_logging_hook: typing.Optional[DebugLoggingHook] = None
        if self._active and debug_logging_enabled():
            self._debug_logging_hook = DebugLoggingHook()
            warn_raw_evaluation_data_logged()

    def get_metadata(self) -> Metadata:
        """Returns provider metadata."""
        return self._metadata

    def attach(self, on_emit: typing.Callable[..., None]) -> None:
        """Attach OpenFeature event dispatch and register for RC callbacks."""
        super().attach(on_emit)
        if self._active:
            _register_provider(self)

    def get_provider_hooks(self) -> list[typing.Any]:
        """
        Returns provider-level hooks.

        The OTel metrics hook is registered here to track metrics for
        every flag evaluation via the finally_after hook stage.

        Hook ordering:
        1. OTel FlagEvalMetricsHook (_flageval_metrics.py) — always registered when the provider
           is enabled; emits the feature_flag.evaluations OTel counter (preserved unchanged).
        2. FlagEvalEVPHook (_flag_eval_evp_hook.py) — registered only when
           DD_FLAGGING_EVALUATION_COUNTS_ENABLED is enabled (default on); enqueues cheap
           snapshots to FlagEvaluationWriter for EVP flagevaluation emission.
        3. SpanEnrichmentHook (_span_enrichment.py) — registered only when
           DD_EXPERIMENTAL_FLAGGING_PROVIDER_SPAN_ENRICHMENT_ENABLED is enabled (default off);
           accumulates feature-flag metadata onto the local-root APM span.
        4. DebugLoggingHook (_debug_logging_hook.py) — registered only when debug logging is
           enabled (DD_TRACE_DEBUG=true); logs the details of every evaluation.
        5. ExposureLoggingHook — records the exposure decision and sends new exposures to
           Datadog. Last in this list so its finally_after runs before every other hook's,
           which then see the final __dd_exposure_cache_hit.
        """
        hooks: list[typing.Any] = []
        if self._flag_eval_metrics_hook is not None:
            hooks.append(self._flag_eval_metrics_hook)
        if self._flag_eval_evp_hook is not None:
            hooks.append(self._flag_eval_evp_hook)
        if self._span_enrichment_hook is not None:
            hooks.append(self._span_enrichment_hook)
        if self._debug_logging_hook is not None:
            hooks.append(self._debug_logging_hook)
        if self._active:
            hooks.append(self._exposure_logging_hook)
        return hooks

    def initialize(self, evaluation_context: EvaluationContext) -> None:
        """
        Initialize the provider, waiting up to the initialization timeout for config.

        Blocking bounds PROVIDER_READY to a provider that can actually resolve flags,
        matching the other server SDKs. A timeout raises ProviderNotReadyError so the
        OpenFeature SDK reports PROVIDER_ERROR instead of a false PROVIDER_READY.
        on_configuration_received() promotes the provider whenever the payload does land.
        Evaluations before that return the caller-provided default with
        ErrorCode.PROVIDER_NOT_READY.

        If config already arrived before initialize() runs (e.g. in the master process of
        a pre-fork server), the fast path sets READY without waiting.

        Provider lifecycle:
            NOT_READY -> initialize() waits -> config arrives -> READY
            NOT_READY -> initialize() waits -> timeout -> ERROR
                      -> config arrives -> on_configuration_received() -> READY
        """
        if not self._active:
            return

        logger.debug(
            "Feature Flags: waiting for provider initialization (timeout %.1fs)...", self._initialization_timeout
        )
        self._initialization_timed_out = False

        # Register for RC config callbacks (in initialize, not __init__, so
        # re-initialization after shutdown re-registers the provider)
        _register_provider(self)

        # Start the agentless poller when agentless is the resolved source
        # (no-op otherwise). Mirrors dd-trace-js starting the source from the
        # provider lifecycle rather than at tracer init.
        deadline = time.monotonic() + self._initialization_timeout
        started = self._start_configuration_source(deadline)
        if started is False:
            # A shutdown, for example from a log handler, cancelled this initialization.
            raise ProviderNotReadyError("The Feature Flagging provider was shut down during initialization")

        try:
            # Start the exposure writer for reporting
            start_exposure_writer()
        except ServiceStatusError:
            logger.debug("Exposure writer is already running", exc_info=True)

        # Start the EVP flagevaluation writer (if enabled via killswitch).
        if self._flag_eval_evp_writer is not None:
            try:
                self._flag_eval_evp_writer.start()
                logger.debug("FlagEvaluationWriter started")
            except ServiceStatusError:
                logger.debug("FlagEvaluationWriter is already running", exc_info=True)

        # Fast path: config already available (RC delivered before set_provider —
        # common in pre-fork servers where master receives RC before workers fork).
        # A snapshot loaded for a released, different flag selection does not count, nor does any snapshot
        # while another thread's start, which may not have claimed its selection yet, is still in progress.
        if started and _get_ffe_snapshot() is not None and not snapshot_from_other_selection():
            self._config_received.set()
            self._status = ProviderStatus.READY
            logger.debug("Feature Flags: provider initialized successfully (configuration was already available)")
            return  # SDK will dispatch PROVIDER_READY

        # Config not yet available — wait for the source to deliver it, so a caller
        # that observes PROVIDER_READY can trust the first evaluation.
        wait_started = time.monotonic()
        # A start that timed out waiting for another thread's start has not finished, so it is not ready
        # whatever the signal says; that other start's initialize() reports READY once it has.
        if started and self._config_received.wait(timeout=max(0.0, deadline - wait_started)):
            self._status = ProviderStatus.READY
            logger.debug(
                "Feature Flags: provider initialized successfully (configuration received after %.2fs)",
                time.monotonic() - wait_started,
            )
            return  # SDK will dispatch PROVIDER_READY

        # the wait above must stay bounded. A blocked initialize() blocks
        # set_provider() in openfeature-sdk 0.8.x and set_provider_and_wait() in 0.10+.
        # The 10s default stays inside gunicorn's 30s worker timeout. Raising the
        # OpenFeature error is also required: the SDK converts it to PROVIDER_ERROR for
        # non-blocking registration and propagates it for blocking registration.
        # Warning rather than error: errors are forwarded to telemetry, and a routine
        # initialization timeout should not inflate its volume.
        if _get_ffe_snapshot() is None:
            logger.warning(
                "Feature Flags: provider failed to initialize: no configuration received after %.1fs; "
                "evaluations will return default values until configuration arrives",
                self._initialization_timeout,
            )
        else:
            # Only a snapshot loaded for a released, different flag selection is left here.
            logger.warning(
                "Feature Flags: provider failed to initialize: no configuration received after %.1fs; "
                "evaluations use the configuration loaded for the previous flag selection until configuration "
                "arrives",
                self._initialization_timeout,
            )
        self._initialization_timed_out = True
        raise ProviderNotReadyError("No Feature Flagging configuration received before the initialization timeout")

    def shutdown(self) -> None:
        """
        Shutdown the provider.

        Called by the OpenFeature SDK when the provider is being replaced or shutdown.
        """
        if not self._active:
            return

        logger.debug("Feature Flags: shutting down provider")

        # Detach and drop readiness before the poller's teardown, which can raise: a signal left set would
        # otherwise make a later initialize() report READY on configuration from before this shutdown.
        _unregister_provider(self)
        self._status = ProviderStatus.NOT_READY
        self._config_received.clear()

        # Stop the agentless poller if it was started.
        self._stop_configuration_source()

        try:
            # Stop the exposure writer
            stop_exposure_writer()
        except ServiceStatusError:
            logger.debug("Exposure writer has already stopped", exc_info=True)

        # Stop the EVP flagevaluation writer (if it was started).
        if self._flag_eval_evp_writer is not None:
            try:
                self._flag_eval_evp_writer.stop()
                # Bounded join: the final flush runs inside the worker's on_shutdown, so
                # a hung agent connection here would otherwise block process exit until
                # the orchestrator kills it.
                self._flag_eval_evp_writer.join(timeout=DRAIN_WORKER_JOIN_TIMEOUT)
                logger.debug("FlagEvaluationWriter stopped")
            except ServiceStatusError:
                logger.debug("FlagEvaluationWriter has already stopped", exc_info=True)
            self._flag_eval_evp_writer = None
            self._flag_eval_evp_hook = None

        reset_feature_flag_evp_route_selector()

        # Shutdown flag evaluation metrics
        if self._flag_eval_metrics is not None:
            self._flag_eval_metrics.shutdown()
            self._flag_eval_metrics = None
            self._flag_eval_metrics_hook = None

        # Tear down the span-enrichment hook: unsubscribe the span-finish
        # callback (symmetric subscribe<->unsubscribe -- avoids a duplicate
        # subscription on provider reconfigure).
        if self._span_enrichment_hook is not None:
            self._span_enrichment_hook.destroy()
            self._span_enrichment_hook = None

        # Clear exposure cache
        self.clear_exposure_cache()

        # Unregister provider
        _unregister_provider(self)
        logger.debug(
            "Feature Flags: provider configuration callbacks detached; shared Remote Config delivery, if enabled, "
            "stops with the tracer"
        )
        self._status = ProviderStatus.NOT_READY
        self._config_received.clear()
        self._initialization_timed_out = False
        logger.debug("Feature Flags: provider shut down; see writer shutdown outcomes for delivery or unfinished work")

    def _start_configuration_source(self, deadline: float) -> typing.Optional[bool]:
        """Start the agentless poller when agentless is the resolved source.

        Returns True when the poller runs (or no agentless poller applies), False when a shutdown after this
        call began cancelled it, and None when ``deadline`` (a ``time.monotonic()`` value) passed while it
        waited. A start from another thread during an in-flight start, or during a teardown of retired
        pollers, waits for it and then checks again. A nested start on the same thread, such as one from a
        log handler, returns at once. A start during the teardown of the current poller adds nothing.
        """
        me = current_thread()
        with self._configuration_source_lock:
            shutdowns = self._configuration_source_shutdowns
        while True:
            with self._configuration_source_lock:
                if self._configuration_source_shutdowns != shutdowns:
                    return False
                if self._configuration_source is not None:
                    return True
                if self._stopping_configuration_source:
                    # Only retired pollers are being torn down. A start must not add a poller that teardown
                    # does not own, so it waits, unless it is nested in that teardown on the same thread.
                    if self._configuration_source_stopper is me:
                        return False
                elif not self._starting_configuration_source:
                    self._starting_configuration_source = True
                    self._configuration_source_starter = (me, shutdowns)
                    break
                else:
                    starter, starter_shutdowns = self._configuration_source_starter
                    if starter is me:
                        # It cannot wait for the start it is nested in, which a shutdown since then cancels.
                        return bool(starter_shutdowns == shutdowns)
                changed = self._configuration_source_changed
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            changed.wait(remaining)

        with _lifecycle_operation(self):
            return self._start_reserved_configuration_source(shutdowns)

    def _start_reserved_configuration_source(self, shutdowns: int) -> bool:
        from ddtrace.internal.openfeature._source_selection import create_agentless_source

        source = None
        started = False
        try:
            source = create_agentless_source(ffe_config, _apply_agentless_configuration, self._flag_selection)
            if source is not None:
                with self._configuration_source_lock:
                    self._pending_configuration_source = source
            if source is not None and self._configuration_source_shutdowns == shutdowns:
                # A poller of the released selection may have notified this provider after attach()
                # registered it. Nothing under the new claim has polled yet, so no notification is lost.
                if snapshot_from_other_selection():
                    self._config_received.clear()
                    self._status = ProviderStatus.NOT_READY
                try:
                    source.start()
                except ServiceStatusError:
                    logger.debug("Agentless configuration source is already running", exc_info=True)
                started = True
        except BaseException:
            with self._configuration_source_lock:
                self._pending_configuration_source = None
                self._end_configuration_source_start()
                if source is not None:
                    release_process_selection()
            raise

        with self._configuration_source_lock:
            cancelled = self._configuration_source_shutdowns != shutdowns
            if source is not None and not cancelled:
                self._configuration_source = source
                self._pending_configuration_source = None
            # In the same step as storing the source, so a shutdown sees either the start or the source. This
            # wakes waiting starts before any teardown below: one may be a callback on the poller being joined.
            self._end_configuration_source_start()
        if source is None or not cancelled:
            return not cancelled

        try:
            if started:
                self._stop_and_join(source)
        except BaseException:
            with self._configuration_source_lock:
                self._retired_configuration_sources.append(source)
                self._pending_configuration_source = None
            raise
        with self._configuration_source_lock:
            self._pending_configuration_source = None
            release_process_selection()
        return False

    def _end_configuration_source_start(self) -> None:
        """End the start in progress and wake waiting starts. Call with the lifecycle lock held."""
        self._starting_configuration_source = False
        self._configuration_source_starter = None
        self._wake_configuration_source_waiters()

    def _wake_configuration_source_waiters(self) -> None:
        """Wake starts waiting for a change. Call with the lifecycle lock held."""
        woken, self._configuration_source_changed = self._configuration_source_changed, Event()
        woken.set()

    @staticmethod
    def _stop_and_join(source: "AgentlessConfigurationSource") -> None:
        try:
            source.stop()
        except ServiceStatusError:
            logger.debug("Agentless configuration source is already stopped", exc_info=True)
        source.join()

    def _stop_configuration_source(self) -> None:
        """Stop the agentless poller and any retired one, or cancel a start in progress.

        A poller whose teardown fails is retired: it keeps its claim, and the next shutdown retries it.
        """
        with _lifecycle_operation(self):
            with self._configuration_source_lock:
                self._configuration_source_shutdowns += 1
                self._wake_configuration_source_waiters()
                if self._starting_configuration_source or self._stopping_configuration_source:
                    return
                source = self._configuration_source
                sources = self._retired_configuration_sources + ([source] if source is not None else [])
                if not sources:
                    return
                self._stopping_configuration_source = True
                self._configuration_source_stopper = current_thread()

            failed = []
            error: typing.Optional[BaseException] = None
            for each in sources:
                try:
                    self._stop_and_join(each)
                except BaseException as e:
                    failed.append(each)
                    if error is None:
                        error = e
            with self._configuration_source_lock:
                # The pollers stay on the provider until here, so a start during the teardown adds no poller and
                # a forked child can see every poller whose claim is still held.
                if source is not None:
                    self._configuration_source = None
                retired = [each for each in self._retired_configuration_sources if each not in sources]
                self._retired_configuration_sources = retired + failed
                self._stopping_configuration_source = False
                self._configuration_source_stopper = None
                self._wake_configuration_source_waiters()
                for _ in range(len(sources) - len(failed)):
                    release_process_selection()
        if error is not None:
            raise error
        logger.debug("Feature Flags: agentless configuration source stopped")

    def _discard_configuration_sources_in_child(self) -> None:
        """Forget, in a forked child, every poller of a start or teardown that a parent thread was running.

        None of them runs in the child and none of their claims counts there, as if the teardown had finished.
        """
        sources = [self._configuration_source, self._pending_configuration_source, *self._retired_configuration_sources]
        for each in sources:
            if each is not None:
                each.discard_in_child()
        self._configuration_source = None
        self._pending_configuration_source = None
        self._retired_configuration_sources = []
        self._starting_configuration_source = False
        self._configuration_source_starter = None
        self._stopping_configuration_source = False
        self._configuration_source_stopper = None
        self._configuration_source_operations = []

    def _claims_held(self) -> int:
        current = [getattr(self, "_configuration_source", None), getattr(self, "_pending_configuration_source", None)]
        return sum(each is not None for each in current) + len(getattr(self, "_retired_configuration_sources", []))

    def resolve_boolean_details(
        self,
        flag_key: str,
        default_value: bool,
        evaluation_context: typing.Optional[EvaluationContext] = None,
    ) -> FlagResolutionDetails[bool]:
        return self._resolve_details(flag_key, default_value, evaluation_context, VariationType.Boolean)

    def resolve_string_details(
        self,
        flag_key: str,
        default_value: str,
        evaluation_context: typing.Optional[EvaluationContext] = None,
    ) -> FlagResolutionDetails[str]:
        return self._resolve_details(flag_key, default_value, evaluation_context, VariationType.String)

    def resolve_integer_details(
        self,
        flag_key: str,
        default_value: int,
        evaluation_context: typing.Optional[EvaluationContext] = None,
    ) -> FlagResolutionDetails[int]:
        return self._resolve_details(flag_key, default_value, evaluation_context, VariationType.Integer)

    def resolve_float_details(
        self,
        flag_key: str,
        default_value: float,
        evaluation_context: typing.Optional[EvaluationContext] = None,
    ) -> FlagResolutionDetails[float]:
        return self._resolve_details(flag_key, default_value, evaluation_context, VariationType.Float)

    def resolve_object_details(
        self,
        flag_key: str,
        default_value: ObjectFlagValue,
        evaluation_context: typing.Optional[EvaluationContext] = None,
    ) -> FlagResolutionDetails[ObjectFlagValue]:
        return self._resolve_details(flag_key, default_value, evaluation_context, VariationType.Object)

    def _resolve_details(
        self,
        flag_key: str,
        default_value: ResolvedValue,
        evaluation_context: typing.Optional[EvaluationContext] = None,
        variation_type: VariationType = VariationType.Boolean,
    ) -> FlagResolutionDetails[ResolvedValue]:
        snapshot = _get_ffe_snapshot()
        details = self._resolve(flag_key, default_value, evaluation_context, variation_type, snapshot)
        if self._debug_logging_hook is not None:
            log_provider_resolution(
                flag_key,
                variation_type,
                default_value,
                evaluation_context,
                details,
                snapshot.identity if snapshot is not None else None,
            )
        return details

    def _resolve(
        self,
        flag_key: str,
        default_value: ResolvedValue,
        evaluation_context: typing.Optional[EvaluationContext],
        variation_type: VariationType,
        snapshot: typing.Optional[_FfeSnapshot],
    ) -> FlagResolutionDetails[ResolvedValue]:
        """
        Core resolution logic for all flag types.

        Follows OpenFeature spec:
        - Returns flag value with reason and variant on success
        - Returns default value with DEFAULT reason when no configuration is available
        - Returns default value with ERROR reason and FLAG_NOT_FOUND error_code when
          flag is not found in the configuration
        - Returns error with error_code and error_message on other errors
        """
        # Capture one snapshot at provider entry. Every exit path
        # carries consent from the exact configuration used for resolution;
        # downstream code must never reread live configuration.
        observe_full_evaluation_data = snapshot.observe_full_evaluation_data if snapshot is not None else False
        flag_metadata = FlagMetadata()
        flag_metadata[EVAL_TIMESTAMP_METADATA_KEY] = int(time.time() * 1000)
        flag_metadata[METADATA_OBSERVE_FULL_EVALUATION_DATA] = observe_full_evaluation_data
        flag_metadata._dd_evaluation_context = evaluation_context

        # If provider is not active, return default value
        if not self._active:
            return FlagResolutionDetails(
                value=default_value,
                reason=Reason.DISABLED,
                variant=None,
                flag_metadata=flag_metadata,
            )

        try:
            # Resolve against the same snapshot that supplied consent.
            config = snapshot.config if snapshot is not None else None

            # Filter once: the exposure event reuses the same attributes. Other context
            # shapes keep being filtered where they are read.
            native_attributes = (
                _native_attributes(evaluation_context.attributes or {})
                if config is not None and isinstance(evaluation_context, EvaluationContext)
                else None
            )

            # Resolve flag using native implementation
            details = resolve_flag(
                config,
                flag_key=flag_key,
                context=evaluation_context,
                expected_type=variation_type,
                attributes=native_attributes,
            )

            # No configuration available - return error with PROVIDER_NOT_READY code
            # Note: No exposure logging when configuration is missing
            if details is None:
                return FlagResolutionDetails(
                    value=default_value,
                    reason=Reason.ERROR,
                    error_code=ErrorCode.PROVIDER_NOT_READY,
                    error_message="No FFE configuration loaded",
                    flag_metadata=flag_metadata,
                )

            # Handle errors from native evaluation
            if details.error_code is not None:
                # Map native error code to OpenFeature error code
                openfeature_error_code = self._map_error_code_to_openfeature(details.error_code)

                # Flag not found - return default with ERROR reason and error_code
                if details.error_code == ffe.ErrorCode.FlagNotFound:
                    return FlagResolutionDetails(
                        value=default_value,
                        reason=Reason.ERROR,
                        error_code=openfeature_error_code,
                        error_message="Flag not found",
                        flag_metadata=flag_metadata,
                    )

                # Other errors - return default with ERROR reason
                return FlagResolutionDetails(
                    value=default_value,
                    reason=Reason.ERROR,
                    error_code=openfeature_error_code,
                    error_message=details.error_message or "Unknown error",
                    flag_metadata=flag_metadata,
                )

            # Map native ffe.Reason to OpenFeature Reason
            reason = self._map_reason_to_openfeature(details.reason)

            # The native getter builds new tuples on every read.
            split_features = details.features
            add_split_features(flag_metadata, split_features)

            if details.do_log:
                self._prepare_exposure_decision(
                    flag_metadata,
                    flag_key=flag_key,
                    variant_key=details.variant,
                    allocation_key=details.allocation_key,
                    evaluation_context=evaluation_context,
                    serial_id=details.serial_id,
                    has_split_features=split_features is not None,
                    attributes=native_attributes,
                )
            else:
                logger.debug(
                    "Feature Flags: exposure not recorded for %s: exposure logging is disabled for this "
                    "evaluation (allocation=%r)",
                    flag_key,
                    details.allocation_key,
                )

            # Add allocation_key to the provider-entry timestamp metadata when present.
            if details.allocation_key:
                flag_metadata[METADATA_ALLOCATION_KEY] = details.allocation_key

            # Thread serial id + do_log into flag_metadata for span enrichment.
            # The span-enrichment hook reads these from details.flag_metadata.
            if details.serial_id is not None:
                flag_metadata[METADATA_SERIAL_ID] = details.serial_id
            flag_metadata[METADATA_DO_LOG] = details.do_log

            # Check if variant is None/empty to determine if we should use default value.
            # For JSON flags, value can be null which is valid, so we check variant instead.
            # We preserve the reason from evaluation (could be DEFAULT, DISABLED, etc.)
            if not details.variant:
                return FlagResolutionDetails(
                    value=default_value,
                    reason=reason,
                    variant=None,
                    flag_metadata=flag_metadata,
                )

            # Success - return resolved value (which may be None for JSON flags)
            return FlagResolutionDetails(
                value=typing.cast(ResolvedValue, details.value),
                reason=reason,
                variant=details.variant,
                flag_metadata=flag_metadata,
            )

        except Exception as e:
            # Unexpected errors
            return FlagResolutionDetails(
                value=default_value,
                reason=Reason.ERROR,
                error_code=ErrorCode.GENERAL,
                error_message=f"Unexpected error during flag evaluation: {str(e)}",
                flag_metadata=flag_metadata,
            )

    def _prepare_exposure_decision(
        self,
        flag_metadata: FlagMetadata,
        flag_key: str,
        variant_key: typing.Optional[str],
        allocation_key: typing.Optional[str],
        evaluation_context: typing.Optional[EvaluationContext],
        serial_id: typing.Optional[int] = None,
        has_split_features: bool = False,
        attributes: typing.Any = None,
    ) -> None:
        """
        Stamp a provisional exposure-cache verdict and attach the pending decision.

        Nothing is recorded or sent here: ExposureLoggingHook (or ExposureHook) completes
        the decision once the evaluation has succeeded, after every after hook ran.
        The cache maps (flag_key, subject_id) to (allocation_key, variant_key, serial_id).
        """
        try:
            exposure_event = build_exposure_event(
                flag_key=flag_key,
                variant_key=variant_key,
                allocation_key=allocation_key,
                evaluation_context=evaluation_context,
                serial_id=serial_id,
                attributes=attributes,
            )
            if not exposure_event:
                return
            # Features stay off the cache value: a features change alone is not a new exposure.
            if has_split_features:
                features = features_with_prefix(flag_metadata, METADATA_EXPOSURE_FEATURE_PREFIX)
                if features:
                    exposure_event["features"] = features
            decision = ExposureDecision(
                self._exposure_cache,
                self._exposure_cache_lock,
                (flag_key, exposure_event["subject"]["id"]),
                (allocation_key, variant_key, serial_id),
                exposure_event,
            )
            flag_metadata[METADATA_EXPOSURE_CACHE_HIT] = decision.is_cache_hit()
            flag_metadata._dd_exposure_decision = decision
        except Exception as e:
            logger.debug("Failed to prepare exposure decision: %s", e, exc_info=True)

    def _map_reason_to_openfeature(self, native_reason) -> Reason:
        """Map native ffe.Reason to OpenFeature Reason."""
        # Handle string reasons from fallback dict implementation
        if isinstance(native_reason, str):
            string_map = {
                "STATIC": Reason.STATIC,
                "TARGETING_MATCH": Reason.TARGETING_MATCH,
                "SPLIT": Reason.SPLIT,
            }
            return string_map.get(native_reason, Reason.UNKNOWN)

        # Map native ffe.Reason enum to OpenFeature Reason
        if native_reason == ffe.Reason.Static:
            return Reason.STATIC
        elif native_reason == ffe.Reason.TargetingMatch:
            return Reason.TARGETING_MATCH
        elif native_reason == ffe.Reason.Split:
            return Reason.SPLIT
        elif native_reason == ffe.Reason.Default:
            return Reason.DEFAULT
        elif native_reason == ffe.Reason.Cached:
            return Reason.CACHED
        elif native_reason == ffe.Reason.Disabled:
            return Reason.DISABLED
        elif native_reason == ffe.Reason.Error:
            return Reason.ERROR
        elif native_reason == ffe.Reason.Stale:
            return Reason.STALE
        else:
            return Reason.UNKNOWN

    def _map_error_code_to_openfeature(self, native_error_code) -> ErrorCode:
        """Map native ffe.ErrorCode to OpenFeature ErrorCode."""
        if native_error_code == ffe.ErrorCode.TypeMismatch:
            return ErrorCode.TYPE_MISMATCH
        elif native_error_code == ffe.ErrorCode.ParseError:
            return ErrorCode.PARSE_ERROR
        elif native_error_code == ffe.ErrorCode.FlagNotFound:
            return ErrorCode.FLAG_NOT_FOUND
        elif native_error_code == ffe.ErrorCode.TargetingKeyMissing:
            return ErrorCode.TARGETING_KEY_MISSING
        elif native_error_code == ffe.ErrorCode.InvalidContext:
            return ErrorCode.INVALID_CONTEXT
        elif native_error_code == ffe.ErrorCode.ProviderNotReady:
            return ErrorCode.PROVIDER_NOT_READY
        elif native_error_code == ffe.ErrorCode.General:
            return ErrorCode.GENERAL
        else:
            return ErrorCode.GENERAL

    def on_configuration_received(self) -> None:
        """
        Called when a Remote Configuration payload is received and processed.

        Updates status first, then signals the event for observers.
        Emits PROVIDER_READY for late arrivals after initialization fails.
        """
        if not self._config_received.is_set():
            self._status = ProviderStatus.READY
            # The SDK emits READY after successful initialization. The provider only
            # owns the recovery event after initialization reported an error.
            if self._initialization_timed_out:
                logger.debug("Feature Flags: provider recovered: configuration received after initialization timed out")
                self._emit_ready_event()
            else:
                logger.debug("First FFE configuration received, provider is now READY")

        # Signal the event last after status is updated.
        self._config_received.set()

    def _emit_ready_event(self) -> None:
        """
        Safely emit PROVIDER_READY event.

        Handles SDK version compatibility - emit_provider_ready() only exists in SDK 0.7.0+.
        """
        if hasattr(self, "emit_provider_ready") and ProviderEventDetails is not None:
            self.emit_provider_ready(ProviderEventDetails())
        else:
            # SDK 0.6.0 doesn't have emit methods
            logger.debug("Provider status is READY (event emission not supported in SDK 0.6.0)")

    def clear_exposure_cache(self) -> None:
        """
        Clear the exposure event cache.

        This method is useful for testing to ensure fresh exposure events are sent.
        """
        with self._exposure_cache_lock:
            self._exposure_cache.clear()
        logger.debug("Exposure cache cleared")


# A plain lock is much cheaper than forksafe.Lock() on every evaluation, so the provider
# resets its own lock in a forked child instead. The reset is in place because pending
# exposure decisions keep a reference to the lock.
_providers_with_exposure_cache: "weakref.WeakSet[DataDogProvider]" = weakref.WeakSet()


# How long a fork waits for a provider start or teardown in progress. By then the fork has joined every periodic
# thread, so only their bookkeeping is left; a fork that stops waiting resets the provider in the child instead.
_FORK_WAIT_SECONDS = 1.0
_lifecycle_lock = Lock()
# Replaced, and set, when a start or teardown ends, so a waiting fork checks again.
_lifecycle_ended = Event()


@contextmanager
def _lifecycle_operation(provider: DataDogProvider) -> typing.Iterator[None]:
    global _lifecycle_ended
    owner = current_thread()
    with _lifecycle_lock:
        provider._configuration_source_operations.append(owner)
    try:
        yield
    finally:
        with _lifecycle_lock:
            provider._configuration_source_operations.remove(owner)
            ended, _lifecycle_ended = _lifecycle_ended, Event()
        ended.set()


def _operations(provider: DataDogProvider) -> list[typing.Any]:
    # A provider another thread is still constructing at the fork is in the set before it has this field.
    return getattr(provider, "_configuration_source_operations", [])


def _another_thread_in_lifecycle(me: typing.Any) -> bool:
    providers = list(_providers_with_exposure_cache)
    return any(owner is not me for provider in providers for owner in _operations(provider))


@forksafe.register_before_fork
def _wait_for_lifecycle_before_fork() -> None:
    """Let a start or teardown on another thread finish, so the child sees its claim with the poller holding it."""
    me = current_thread()
    deadline = time.monotonic() + _FORK_WAIT_SECONDS
    while True:
        with _lifecycle_lock:
            if not _another_thread_in_lifecycle(me):
                return
            ended = _lifecycle_ended
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.debug("Feature Flags: forking during a provider start or teardown; the child resets that provider")
            return
        ended.wait(remaining)


@forksafe.register
def _reset_provider_state_in_child() -> typing.Iterator[None]:
    """Reset what parent threads left in progress.

    It runs before the other child hooks, so the thread module does not restart a discarded poller and the claim's
    fork reset sees the recounted claim.
    """
    global _lifecycle_lock, _lifecycle_ended
    _lifecycle_lock = Lock()
    _lifecycle_ended = Event()
    me = current_thread()
    providers = list(_providers_with_exposure_cache)
    reset = False
    for provider in providers:
        provider._exposure_cache_lock._at_fork_reinit()  # type: ignore[attr-defined]
        # A parent thread may have held the event's internal lock at the fork, and no parent waiter survives.
        provider._configuration_source_changed = Event()
        starter = getattr(provider, "_configuration_source_starter", None)
        if starter is None or starter[0] is not me:
            # A start reserved on a parent thread but not yet building its source holds no claim.
            provider._starting_configuration_source = False
            provider._configuration_source_starter = None
        owners = _operations(provider)
        if owners and me not in owners:
            provider._discard_configuration_sources_in_child()
            reset = True
    # The forking thread's own start or teardown carries on here with a claim the child cannot see, so the
    # inherited count stays: it can only be too high, which keeps the inherited selection.
    if reset and not any(me in _operations(provider) for provider in providers):
        recount_process_selection_in_child(sum(provider._claims_held() for provider in providers))
    yield


class ExposureLoggingHook(Hook):
    """
    Records the exposure decision once the evaluation has succeeded and sends new
    exposures to Datadog.

    It runs at the finally_after stage because the SDK runs after hooks first: an after
    hook that raises turns the result into an error, so a failed evaluation sends nothing
    and records nothing. The cache records even when Datadog logging is off, so
    ExposureHook sees the same verdicts either way. Delivery to Datadog is best-effort:
    the exposure is recorded before the send, so a failed enqueue is not retried.
    """

    def __init__(self) -> None:
        self._warned = False

    def finally_after(
        self,
        hook_context: HookContext,
        details: FlagEvaluationDetails[typing.Any],
        hints: HookHints,
    ) -> None:
        try:
            decision = complete_exposure_decision(details)
            if decision is None:
                return
            if details.flag_metadata.get(METADATA_EXPOSURE_CACHE_HIT) is not False:
                logger.debug("Skipping duplicate exposure event for %s", details.flag_key)
                return
            get_exposure_writer().enqueue(decision.event)
        except Exception:
            if not self._warned:
                self._warned = True
                logger.warning("Could not record a feature flag exposure; later failures are not logged", exc_info=True)


def _apply_agentless_configuration(configuration: "dict[str, typing.Any]") -> None:
    """Apply a UFC payload delivered by the agentless source.

    ``process_ffe_configuration`` reports a payload the native evaluator refused
    by returning False rather than raising, which the agentless source would
    otherwise read as success and advance its ETag past a configuration it never
    loaded (the next poll would then get a 304 and keep the stale config
    indefinitely). Translate a rejection into an error so the source keeps
    last-known-good and retries the payload on the next poll.
    """
    from ddtrace.internal.openfeature._native import process_ffe_configuration

    if not process_ffe_configuration(configuration):
        raise ValueError("Feature Flagging configuration was rejected by the evaluator")
