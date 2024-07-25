import mongoengine

from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate

from .trace import WrappedConnect


# Original connect function
_connect = mongoengine.connect


def _get_version():
    # type: () -> str
    return getattr(mongoengine, "__version__", "")


def get_version():
    deprecate(
        "get_version is deprecated",
        message="get_version is deprecated",
        removal_version="3.0.0",
        category=DDTraceDeprecationWarning,
    )
    return _get_version()


def patch():
    mongoengine.connect = WrappedConnect(_connect)


def unpatch():
    mongoengine.connect = _connect
