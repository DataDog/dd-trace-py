from importlib.metadata import version
import typing

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

pkg_version = version("openfeature-sdk")

if pkg_version:
    from ddtrace.internal.openfeature._provider import DataDogProvider as DataDogProvider
else:
    # OpenFeature SDK is not installed - provide stub implementation
    class DataDogProvider:  # type: ignore[no-redef]
        """
        Stub DataDogProvider when openfeature-sdk is not installed.

        Logs an error when instantiated, informing users to install the openfeature-sdk package.
        """

        def __init__(self, *args: typing.Any, **kwargs: typing.Any):
            log.error(
                "openfeature-sdk not installed. Please install openfeature-sdk first. "
                "Check the official documentation: https://openfeature.dev/docs/reference/technologies/server/python"
            )

        def shutdown(self):
            pass

        def initialize(self, evaluation_context) -> None:
            pass

        def get_provider_hooks(self):
            return []

        def resolve_string_details(self, *args, **kwargs):
            pass

        def resolve_boolean_details(self, *args, **kwargs):
            pass

        def resolve_integer_details(self, *args, **kwargs):
            pass

        def resolve_float_details(self, *args, **kwargs):
            pass

        def resolve_object_details(self, *args, **kwargs):
            pass
