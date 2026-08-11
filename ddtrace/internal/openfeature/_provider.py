"""
Feature Flagging and Experimentation (FFE) product module.

This module handles Feature Flag configuration rules from Remote Configuration
and forwards the raw bytes to the native FFE processor.
"""

from collections import OrderedDict
from collections.abc import MutableMapping
import threading
import time
import typing


if typing.TYPE_CHECKING:
    from ddtrace.internal.openfeature._agentless_source import AgentlessConfigurationSource

from openfeature.evaluation_context import EvaluationContext
from openfeature.event import ProviderEventDetails
from openfeature.exception import ErrorCode
from openfeature.exception import ProviderNotReadyError
from openfeature.flag_evaluation import FlagResolutionDetails
from openfeature.flag_evaluation import FlagValueType
from openfeature.flag_evaluation import Reason
from openfeature.provider import Metadata
from openfeature.provider import ProviderStatus

from ddtrace.internal.logger import get_logger
from ddtrace.internal.native._native import ffe
from ddtrace.internal.openfeature._config import _get_ffe_config
from ddtrace.internal.openfeature._config import _register_provider
from ddtrace.internal.openfeature._config import _unregister_provider
from ddtrace.internal.openfeature._exposure import build_exposure_event
from ddtrace.internal.openfeature._flag_eval_evp_hook import FlagEvalEVPHook
from ddtrace.internal.openfeature._flageval_metrics import METADATA_ALLOCATION_KEY
from ddtrace.internal.openfeature._flageval_metrics import FlagEvalMetrics
from ddtrace.internal.openfeature._flageval_metrics import FlagEvalMetricsHook
from ddtrace.internal.openfeature._flagevaluation_writer import EVAL_TIMESTAMP_METADATA_KEY
from ddtrace.internal.openfeature._flagevaluation_writer import FlagEvaluationWriter
from ddtrace.internal.openfeature._native import VariationType
from ddtrace.internal.openfeature._native import resolve_flag
from ddtrace.internal.openfeature._span_enrichment import METADATA_DO_LOG
from ddtrace.internal.openfeature._span_enrichment import METADATA_SERIAL_ID
from ddtrace.internal.openfeature._span_enrichment import SpanEnrichmentHook
from ddtrace.internal.openfeature.writer import get_exposure_writer
from ddtrace.internal.openfeature.writer import start_exposure_writer
from ddtrace.internal.openfeature.writer import stop_exposure_writer
from ddtrace.internal.service import ServiceStatusError
from ddtrace.internal.settings.openfeature import OpenFeatureConfig
from ddtrace.internal.settings.openfeature import config as ffe_config


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


class DataDogProvider(AbstractProvider):
    """
    Datadog OpenFeature Provider.

    Implements the OpenFeature provider interface for Datadog's
    Feature Flags and Experimentation (FFE) product.
    """

    def __init__(
        self,
        *args: typing.Any,
        initialization_timeout: typing.Optional[float] = None,
        **kwargs: typing.Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._metadata = Metadata(name="Datadog")
        self._status = ProviderStatus.NOT_READY

        # Event set when the first config arrives; used by on_configuration_received()
        # to guard the first-config path and by _emit_ready_event() timing.
        self._config_received = threading.Event()
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
        # Stores mapping of (flag_key, subject_id) -> (allocation_key, variant_key)
        # Using LRU cache with maxsize of 65536 to prevent unbounded memory growth
        self._exposure_cache: LRUCache[tuple[str, str], tuple[typing.Optional[str], typing.Optional[str]]] = LRUCache(
            maxsize=65536
        )

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

        # Agentless configuration-source poller; started in initialize() when
        # agentless is the resolved source and stopped in shutdown().
        self._configuration_source: typing.Optional["AgentlessConfigurationSource"] = None

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
        # AIDEV-NOTE: the killswitch is read through the ddtrace config system
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
        """
        hooks: list[typing.Any] = []
        if self._flag_eval_metrics_hook is not None:
            hooks.append(self._flag_eval_metrics_hook)
        if self._flag_eval_evp_hook is not None:
            hooks.append(self._flag_eval_evp_hook)
        if self._span_enrichment_hook is not None:
            hooks.append(self._span_enrichment_hook)
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

        self._initialization_timed_out = False

        # Register for RC config callbacks (in initialize, not __init__, so
        # re-initialization after shutdown re-registers the provider)
        _register_provider(self)

        # Start the agentless poller when agentless is the resolved source
        # (no-op otherwise). Mirrors dd-trace-js starting the source from the
        # provider lifecycle rather than at tracer init.
        self._start_configuration_source()

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
        config = _get_ffe_config()
        if config is not None:
            logger.debug("FFE configuration already available, provider is READY")
            self._config_received.set()
            self._status = ProviderStatus.READY
            return  # SDK will dispatch PROVIDER_READY

        # Config not yet available — wait for the source to deliver it, so a caller
        # that observes PROVIDER_READY can trust the first evaluation.
        if self._config_received.wait(timeout=self._initialization_timeout):
            self._status = ProviderStatus.READY
            return  # SDK will dispatch PROVIDER_READY

        # AIDEV-NOTE: the wait above must stay bounded. A blocked initialize() blocks
        # set_provider() in openfeature-sdk 0.8.x and set_provider_and_wait() in 0.10+.
        # The 10s default stays inside gunicorn's 30s worker timeout. Raising the
        # OpenFeature error is also required: the SDK converts it to PROVIDER_ERROR for
        # non-blocking registration and propagates it for blocking registration.
        logger.warning(
            "openfeature: no Feature Flagging configuration received after %.1fs; evaluations will return "
            "default values until configuration arrives",
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
                self._flag_eval_evp_writer.join()
                logger.debug("FlagEvaluationWriter stopped")
            except ServiceStatusError:
                logger.debug("FlagEvaluationWriter has already stopped", exc_info=True)
            self._flag_eval_evp_writer = None
            self._flag_eval_evp_hook = None

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
        self._status = ProviderStatus.NOT_READY
        self._config_received.clear()
        self._initialization_timed_out = False

    def _start_configuration_source(self) -> None:
        """Start the agentless poller when agentless is the resolved source."""
        if self._configuration_source is not None:
            return

        from ddtrace.internal.openfeature._source_selection import create_agentless_source

        source = create_agentless_source(ffe_config, _apply_agentless_configuration)
        if source is None:
            return

        try:
            source.start()
        except ServiceStatusError:
            logger.debug("Agentless configuration source is already running", exc_info=True)
        self._configuration_source = source

    def _stop_configuration_source(self) -> None:
        """Stop the agentless poller if it was started."""
        if self._configuration_source is None:
            return

        try:
            self._configuration_source.stop()
            self._configuration_source.join()
        except ServiceStatusError:
            logger.debug("Agentless configuration source is already stopped", exc_info=True)
        self._configuration_source = None

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
        """
        Core resolution logic for all flag types.

        Follows OpenFeature spec:
        - Returns flag value with reason and variant on success
        - Returns default value with DEFAULT reason when no configuration is available
        - Returns default value with ERROR reason and FLAG_NOT_FOUND error_code when
          flag is not found in the configuration
        - Returns error with error_code and error_message on other errors
        """
        # AIDEV-NOTE: Stamp eval-time at provider entry so every OpenFeature exit path
        # can feed the EVP flagevaluation hook first_evaluation/last_evaluation from
        # evaluation time, not the later hook/flush time.
        flag_metadata: dict[str, typing.Any] = {EVAL_TIMESTAMP_METADATA_KEY: int(time.time() * 1000)}

        # If provider is not active, return default value
        if not self._active:
            return FlagResolutionDetails(
                value=default_value,
                reason=Reason.DISABLED,
                variant=None,
                flag_metadata=flag_metadata,
            )

        try:
            # Get the native Configuration object
            config = _get_ffe_config()

            # Resolve flag using native implementation
            details = resolve_flag(
                config,
                flag_key=flag_key,
                context=evaluation_context,
                expected_type=variation_type,
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
                    # Only report exposure if do_log is explicitly True
                    if details.do_log:
                        self._report_exposure(
                            flag_key=flag_key,
                            variant_key=None,
                            allocation_key=None,
                            evaluation_context=evaluation_context,
                        )
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

            # Report exposure event only if do_log flag is True
            if details.do_log:
                self._report_exposure(
                    flag_key=flag_key,
                    variant_key=details.variant,
                    allocation_key=details.allocation_key,
                    evaluation_context=evaluation_context,
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

    def _report_exposure(
        self,
        flag_key: str,
        variant_key: typing.Optional[str],
        allocation_key: typing.Optional[str],
        evaluation_context: typing.Optional[EvaluationContext],
    ) -> None:
        """
        Report a feature flag exposure event to the EVP proxy intake.

        Uses caching to prevent duplicate exposure events for the same
        (flag_key, subject_id, variant_key, allocation_key) combination.

        Note: This method should only be called when exposure logging is enabled.
        Callers must check the do_log flag before invoking this method.

        Args:
            flag_key: The feature flag key
            variant_key: The variant key returned by evaluation
            allocation_key: The allocation key
            evaluation_context: The evaluation context with subject information
        """
        try:
            exposure_event = build_exposure_event(
                flag_key=flag_key,
                variant_key=variant_key,
                allocation_key=allocation_key,
                evaluation_context=evaluation_context,
            )
            if not exposure_event:
                return

            # Check cache to prevent duplicate exposure events
            key = (flag_key, exposure_event["subject"]["id"])
            value = (allocation_key, variant_key)

            cached_value = self._exposure_cache.get(key, None)
            if cached_value and cached_value == value:
                logger.debug("Skipping duplicate exposure event for %s->%s", key, value)
                return

            writer = get_exposure_writer()
            writer.enqueue(exposure_event)

            # Add to cache only after successful enqueue
            self._exposure_cache[key] = value
        except Exception as e:
            logger.debug("Failed to report exposure event: %s", e, exc_info=True)

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
            logger.debug("First FFE configuration received, provider is now READY")
            # The SDK emits READY after successful initialization. The provider only
            # owns the recovery event after initialization reported an error.
            if self._initialization_timed_out:
                self._emit_ready_event()

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
        self._exposure_cache.clear()
        logger.debug("Exposure cache cleared")


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
