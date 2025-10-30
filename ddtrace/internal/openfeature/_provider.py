"""
Feature Flagging and Experimentation (FFE) product module.

This module handles Feature Flag configuration rules from Remote Configuration
and forwards the raw bytes to the native FFE processor.
"""

import datetime
from importlib.metadata import version
import json
import typing

from openfeature.evaluation_context import EvaluationContext
from openfeature.exception import ErrorCode
from openfeature.flag_evaluation import FlagResolutionDetails
from openfeature.flag_evaluation import Reason
from openfeature.provider import Metadata

from ddtrace.internal.logger import get_logger
from ddtrace.internal.openfeature._config import _get_ffe_config
from ddtrace.internal.openfeature._exposure import build_exposure_event
from ddtrace.internal.openfeature._ffe_mock import AssignmentReason
from ddtrace.internal.openfeature._ffe_mock import EvaluationError
from ddtrace.internal.openfeature._ffe_mock import VariationType
from ddtrace.internal.openfeature._ffe_mock import mock_get_assignment
from ddtrace.internal.openfeature._remoteconfiguration import disable_featureflags_rc
from ddtrace.internal.openfeature._remoteconfiguration import enable_featureflags_rc
from ddtrace.internal.openfeature.writer import get_exposure_writer
from ddtrace.internal.openfeature.writer import start_exposure_writer
from ddtrace.internal.openfeature.writer import stop_exposure_writer
from ddtrace.internal.service import ServiceStatusError
from ddtrace.settings.openfeature import config as ffe_config


# Handle different import paths between openfeature-sdk versions
# Versions 0.7.0+ reorganized submodules
pkg_version = version("openfeature-sdk")
if pkg_version >= "0.7.0":
    from openfeature.provider import AbstractProvider
else:
    from openfeature.provider.provider import AbstractProvider


T = typing.TypeVar("T", covariant=True)
logger = get_logger(__name__)


class DataDogProvider(AbstractProvider):
    """
    Datadog OpenFeature Provider.

    Implements the OpenFeature provider interface for Datadog's
    Feature Flags and Experimentation (FFE) product.
    """

    def __init__(self, *args: typing.Any, **kwargs: typing.Any):
        super().__init__(*args, **kwargs)
        self._metadata = Metadata(name="Datadog")

        # Check if experimental flagging provider is enabled
        self._enabled = ffe_config.experimental_flagging_provider_enabled
        if not self._enabled:
            logger.warning(
                "openfeature: experimental flagging provider is not enabled, "
                "please set DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED=true to enable it",
            )

    def get_metadata(self) -> Metadata:
        """Returns provider metadata."""
        return self._metadata

    def initialize(self, evaluation_context: EvaluationContext) -> None:
        """
        Initialize the provider and enable remote configuration.

        Called by the OpenFeature SDK when the provider is set.
        """
        if not self._enabled:
            return

        enable_featureflags_rc()

        try:
            # Start the exposure writer for reporting
            start_exposure_writer()
        except ServiceStatusError:
            logger.debug("Exposure writer is already running", exc_info=True)

    def shutdown(self) -> None:
        """
        Shutdown the provider and disable remote configuration.

        Called by the OpenFeature SDK when the provider is being replaced or shutdown.
        """
        if not self._enabled:
            return

        disable_featureflags_rc()
        try:
            # Stop the exposure writer
            stop_exposure_writer()
        except ServiceStatusError:
            logger.debug("Exposure writer has already stopped", exc_info=True)

    def resolve_boolean_details(
        self,
        flag_key: str,
        default_value: bool,
        evaluation_context: typing.Optional[EvaluationContext] = None,
    ) -> FlagResolutionDetails[bool]:
        return self._resolve_details(flag_key, default_value, evaluation_context, VariationType.BOOLEAN)

    def resolve_string_details(
        self,
        flag_key: str,
        default_value: str,
        evaluation_context: typing.Optional[EvaluationContext] = None,
    ) -> FlagResolutionDetails[str]:
        return self._resolve_details(flag_key, default_value, evaluation_context, VariationType.STRING)

    def resolve_integer_details(
        self,
        flag_key: str,
        default_value: int,
        evaluation_context: typing.Optional[EvaluationContext] = None,
    ) -> FlagResolutionDetails[int]:
        return self._resolve_details(flag_key, default_value, evaluation_context, VariationType.INTEGER)

    def resolve_float_details(
        self,
        flag_key: str,
        default_value: float,
        evaluation_context: typing.Optional[EvaluationContext] = None,
    ) -> FlagResolutionDetails[float]:
        return self._resolve_details(flag_key, default_value, evaluation_context, VariationType.NUMERIC)

    def resolve_object_details(
        self,
        flag_key: str,
        default_value: typing.Union[dict, list],
        evaluation_context: typing.Optional[EvaluationContext] = None,
    ) -> FlagResolutionDetails[typing.Union[dict, list]]:
        return self._resolve_details(flag_key, default_value, evaluation_context, VariationType.JSON)

    def _resolve_details(
        self,
        flag_key: str,
        default_value: typing.Any,
        evaluation_context: typing.Optional[EvaluationContext] = None,
        variation_type: VariationType = VariationType.BOOLEAN,
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
            config_raw = _get_ffe_config()
            # Parse JSON config if it's a string
            if isinstance(config_raw, str):
                config = json.loads(config_raw) if config_raw else None
            else:
                config = config_raw

            result = mock_get_assignment(
                config,
                flag_key=flag_key,
                subject=evaluation_context,
                expected_type=variation_type,
                now=datetime.datetime.now(),
            )

            # Flag not found or disabled - return default
            if result is None:
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

            # Map AssignmentReason to OpenFeature Reason
            reason_map = {
                AssignmentReason.STATIC: Reason.STATIC,
                AssignmentReason.TARGETING_MATCH: Reason.TARGETING_MATCH,
                AssignmentReason.SPLIT: Reason.SPLIT,
            }
            reason = reason_map.get(result.reason, Reason.UNKNOWN)

            # Report exposure event
            self._report_exposure(
                flag_key=flag_key,
                variant_key=result.variation_key,
                allocation_key=result.variation_key,
                evaluation_context=evaluation_context,
            )

            # Success - return resolved value
            return FlagResolutionDetails(
                value=result.value.value,
                reason=reason,
                variant=result.variation_key,
            )

        except EvaluationError as e:
            # Type mismatch error
            if e.kind == "TYPE_MISMATCH":
                return FlagResolutionDetails(
                    value=default_value,
                    reason=Reason.ERROR,
                    error_code=ErrorCode.TYPE_MISMATCH,
                    error_message=f"Expected {e.expected}, but flag is {e.found}",
                )
            # Other evaluation errors
            return FlagResolutionDetails(
                value=default_value,
                reason=Reason.ERROR,
                error_code=ErrorCode.GENERAL,
                error_message=str(e),
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
        """
        try:
            exposure_event = build_exposure_event(
                flag_key=flag_key,
                variant_key=variant_key,
                allocation_key=allocation_key,
                evaluation_context=evaluation_context,
            )

            if exposure_event:
                writer = get_exposure_writer()
                writer.enqueue(exposure_event)
        except Exception as e:
            logger.debug("Failed to report exposure event: %s", e, exc_info=True)
