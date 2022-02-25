import mongoengine

from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning

from ...vendor.debtcollector.removals import remove
from .trace import WrappedConnect


# Original connect function
_connect = mongoengine.connect


def patch():
    setattr(mongoengine, "connect", WrappedConnect(_connect))


def unpatch():
    setattr(mongoengine, "connect", _connect)


@remove(message="Use patching instead (see the docs).", category=DDTraceDeprecationWarning, removal_version="1.0.0")
def trace_mongoengine(*args, **kwargs):
    return _connect
