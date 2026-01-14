"""
Feature Flagging and Experimentation (FFE) product module.

This module handles Feature Flag configuration rules from Remote Configuration
and forwards the raw bytes to the native FFE processor.
"""

from collections import OrderedDict
from collections.abc import MutableMapping
from importlib.metadata import version
import typing

from openfeature.evaluation_context import EvaluationContext
from openfeature.event import ProviderEventDetails
from openfeature.exception import ErrorCode
from openfeature.flag_evaluation import FlagResolutionDetails
from openfeature.flag_evaluation import Reason
from openfeature.provider import Metadata
from openfeature.provider import ProviderStatus

from ddtrace.internal.logger import get_logger
from ddtrace.internal.native._native import ffe
from ddtrace.internal.openfeature._config import _get_ffe_config
from ddtrace.internal.openfeature._exposure import build_exposure_event
from ddtrace.internal.openfeature._native import VariationType
from ddtrace.internal.openfeature._native import resolve_flag
from ddtrace.internal.openfeature.writer import get_exposure_writer
from ddtrace.internal.openfeature.writer import start_exposure_writer
from ddtrace.internal.openfeature.writer import stop_exposure_writer
from ddtrace.internal.service import ServiceStatusError
from ddtrace.internal.settings.openfeature import config as ffe_config


# Handle different import paths between openfeature-sdk versions
# Versions 0.7.0+ reorganized submodules
pkg_version = version("openfeature-sdk")
if pkg_version >= "0.7.0":
    from openfeature.provider import AbstractProvider
else:
    from openfeature.provider.provider import AbstractProvider


T = typing.TypeVar("T", covariant=True)
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

    def __init__(self, *args: typing.Any, **kwargs: typing.Any):
        super().__init__(*args, **kwargs)
        self._metadata = Metadata(name="Datadog")
        self._status = ProviderStatus.NOT_READY
        self._config_received = False

        # Cache for reported exposures to prevent duplicates
        # Stores mapping of (flag_key, subject_id) -> (allocation_key, variant_key)
        # Using LRU cache with maxsize of 65536 to prevent unbounded memory growth
        self._exposure_cache: LRUCache[
            typing.Tuple[str, str], typing.Tuple[typing.Optional[str], typing.Optional[str]]
        ] = LRUCache(maxsize=65536)

        # Check if experimental flagging provider is enabled
        self._enabled = ffe_config.experimental_flagging_provider_enabled
        if not self._enabled:
            logger.warning(
                "openfeature: experimental flagging provider is not enabled, "
                "please set DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED=true to enable it",
            )

        # Register this provider instance for status updates
        _register_provider(self)

    def get_metadata(self) -> Metadata:
        """Returns provider metadata."""
        return self._metadata

    def initialize(self, evaluation_context: EvaluationContext) -> None:
        """
        Initialize the provider.

        Called by the OpenFeature SDK when the provider is set.
        Provider Creation → NOT_READY
                                 ↓
                   First Remote Config Payload
                                 ↓
                            READY (emits PROVIDER_READY event)
                                 ↓
                           Shutdown
                                 ↓
                          NOT_READY
        """
        if not self._enabled:
            return

        try:
            # Start the exposure writer for reporting
            start_exposure_writer()
        except ServiceStatusError:
            logger.debug("Exposure writer is already running", exc_info=True)

        # If configuration was already received before initialization, emit ready now
        config = _get_ffe_config()
        if config is not None and not self._config_received:
            self._config_received = True
            self._status = ProviderStatus.READY
            self._emit_ready_event()

    def shutdown(self) -> None:
        """
        Shutdown the provider.

        Called by the OpenFeature SDK when the provider is being replaced or shutdown.
        """
        if not self._enabled:
            return

        try:
            # Stop the exposure writer
            stop_exposure_writer()
        except ServiceStatusError:
            logger.debug("Exposure writer has already stopped", exc_info=True)

        # Clear exposure cache
        self.clear_exposure_cache()

        # Unregister provider
        _unregister_provider(self)
        self._status = ProviderStatus.NOT_READY
        self._config_received = False

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
        default_value: typing.Union[dict, list],
        evaluation_context: typing.Optional[EvaluationContext] = None,
    ) -> FlagResolutionDetails[typing.Union[dict, list]]:
        return self._resolve_details(flag_key, default_value, evaluation_context, VariationType.Object)

    def _resolve_details(
        self,
        flag_key: str,
        default_value: typing.Any,
        evaluation_context: typing.Optional[EvaluationContext] = None,
        variation_type: VariationType = VariationType.Boolean,
    ) -> FlagResolutionDetails[T]:
        """
        Core resolution logic for all flag types.

        Follows OpenFeature spec:
        - Returns flag value with reason and variant on success
        - Returns default value with DEFAULT reason when flag not found
        - Returns error with error_code and error_message on errors
        """
        # If provider is not enabled, return default value
        if not self._enabled:
            return FlagResolutionDetails(
                value=default_value,
                reason=Reason.DISABLED,
                variant=None,
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

            # No configuration available - return default
            # Note: No exposure logging when configuration is missing
            if details is None:
                return FlagResolutionDetails(
                    value=default_value,
                    reason=Reason.DEFAULT,
                    variant=None,
                )

            # Handle errors from native evaluation
            if details.error_code is not None:
                # Map native error code to OpenFeature error code
                openfeature_error_code = self._map_error_code_to_openfeature(details.error_code)

                # Flag not found - return default with DEFAULT reason
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
                        reason=Reason.DEFAULT,
                        variant=None,
                    )

                # Other errors - return default with ERROR reason
                return FlagResolutionDetails(
                    value=default_value,
                    reason=Reason.ERROR,
                    error_code=openfeature_error_code,
                    error_message=details.error_message or "Unknown error",
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

            # Check if variant is None/empty to determine if we should use default value.
            # For JSON flags, value can be null which is valid, so we check variant instead.
            # We preserve the reason from evaluation (could be DEFAULT, DISABLED, etc.)
            if not details.variant:
                return FlagResolutionDetails(
                    value=default_value,
                    reason=reason,
                    variant=None,
                )

            # Success - return resolved value (which may be None for JSON flags)
            return FlagResolutionDetails(
                value=details.value,
                reason=reason,
                variant=details.variant,
            )

        except Exception as e:
            # Unexpected errors
            return FlagResolutionDetails(
                value=default_value,
                reason=Reason.ERROR,
                error_code=ErrorCode.GENERAL,
                error_message=f"Unexpected error during flag evaluation: {str(e)}",
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

        Emits PROVIDER_READY event on first configuration.
        """
        if not self._config_received:
            self._config_received = True
            self._status = ProviderStatus.READY
            logger.debug("First FFE configuration received, provider is now READY")
            self._emit_ready_event()

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


# Module-level registry for active provider instances
_provider_instances: typing.List[DataDogProvider] = []


def _register_provider(provider: DataDogProvider) -> None:
    """Register a provider instance for configuration callbacks."""
    if provider not in _provider_instances:
        _provider_instances.append(provider)


def _unregister_provider(provider: DataDogProvider) -> None:
    """Unregister a provider instance."""
    if provider in _provider_instances:
        _provider_instances.remove(provider)


def _notify_providers_config_received() -> None:
    """Notify all registered providers that configuration was received."""
    for provider in _provider_instances:
        provider.on_configuration_received()
