from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate

from ..internal.django._asgi import *  # noqa: F401,F403


def __getattr__(name):
    deprecate(
        ("%s.%s is deprecated" % (__name__, name)),
        category=DDTraceDeprecationWarning,
    )

    if name in globals():
        return globals()[name]
    raise AttributeError("%s has no attribute %s", __name__, name)
