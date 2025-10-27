"""
Feature Flagging and Experimentation (FFAndE) product module.

This module handles Feature Flag configuration rules from Remote Configuration
and forwards the raw bytes to the native FFAndE processor.
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

from ddtrace.featureflags import enable_featureflags_rc
from ddtrace.featureflags._config import _get_ffe_config
from ddtrace.featureflags._ffe_mock import AssignmentReason
from ddtrace.featureflags._ffe_mock import EvaluationError
from ddtrace.featureflags._ffe_mock import VariationType
from ddtrace.featureflags._ffe_mock import mock_get_assignment
from ddtrace.featureflags._remoteconfiguration import disable_featureflags_rc


# Handle different import paths between openfeature-sdk versions
# Versions 0.7.0+ reorganized submodules
pkg_version = version("openfeature-sdk")
if pkg_version >= "0.7.0":
    from openfeature.provider import AbstractProvider
else:
    from openfeature.provider.provider import AbstractProvider


T = typing.TypeVar("T", covariant=True)


class DataDogProvider(AbstractProvider):
    """
    Datadog OpenFeature Provider.

    Implements the OpenFeature provider interface for Datadog's
    Feature Flags and Experimentation (FFAndE) product.
    """

    def __init__(self, *args: typing.Any, **kwargs: typing.Any):
        super().__init__(*args, **kwargs)
        self._metadata = Metadata(name="Datadog")

    def get_metadata(self) -> Metadata:
        """Returns provider metadata."""
        return self._metadata

    def initialize(self, evaluation_context: EvaluationContext) -> None:
        """
        Initialize the provider and enable remote configuration.

        Called by the OpenFeature SDK when the provider is set.
        """
        enable_featureflags_rc()

    def shutdown(self) -> None:
        """
        Shutdown the provider and disable remote configuration.

        Called by the OpenFeature SDK when the provider is being replaced or shutdown.
        """
        disable_featureflags_rc()

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
