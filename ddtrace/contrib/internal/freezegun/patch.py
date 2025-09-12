from typing import Dict

from ddtrace import DDTraceDeprecationWarning
from ddtrace.internal.logger import get_logger
from ddtrace.vendor.debtcollector import deprecate


log = get_logger(__name__)

DDTRACE_MODULE_NAME = "ddtrace"


def get_version() -> str:
    import freezegun

    try:
        return freezegun.__version__
    except AttributeError:
        log.debug("Could not get freezegun version")
        return ""


def _supported_versions() -> Dict[str, str]:
    return {"freezegun": "*"}


def patch() -> None:
    deprecate(
        "the freezegun integration is deprecated",
        message="this integration is not needed anymore for the correct reporting of span durations.",
        removal_version="4.0.0",
        category=DDTraceDeprecationWarning,
    )


def unpatch() -> None:
    pass
