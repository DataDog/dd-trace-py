import logging
import sys
import typing as t

from ddtrace.internal.utils.constants import DEFAULT_SERVICE_NAME
from ddtrace.internal.settings import env
from ddtrace.internal.settings._inferred_base_service import detect_service
from ddtrace.internal.utils.formats import asbool


VALID_VERSIONS = ["v0", "v1"]


log = logging.getLogger(__name__)


def _validate_schema(version: str) -> bool:
    error_message = (
        "You have specified an invalid span attribute schema version: '{}'.".format(version),
        "Valid options are: {}. You can change the specified value by updating".format(VALID_VERSIONS),
        "the value exported in the 'DD_TRACE_SPAN_ATTRIBUTE_SCHEMA' environment variable.",
    )

    if version not in VALID_VERSIONS:
        log.warning(" ".join(error_message))
        return False

    return True


def _get_schema_version() -> t.Any:
    version = env.get("DD_TRACE_SPAN_ATTRIBUTE_SCHEMA", default="v0")
    if not _validate_schema(version):
        version = "v0"
    return version


SCHEMA_VERSION = _get_schema_version()
_remove_client_service_names = asbool(env.get("DD_TRACE_REMOVE_INTEGRATION_SERVICE_NAMES_ENABLED", default=False))
_service_name_schema_version = "v0" if SCHEMA_VERSION == "v0" and not _remove_client_service_names else "v1"

_inferred_base_service: t.Optional[str] = detect_service(sys.argv)


_DEFAULT_SPAN_SERVICE_NAMES: dict[str, t.Optional[str]] = {
    "v0": _inferred_base_service or None,
    "v1": _inferred_base_service or DEFAULT_SERVICE_NAME,
}

DEFAULT_SPAN_SERVICE_NAME = _DEFAULT_SPAN_SERVICE_NAMES[_service_name_schema_version]
