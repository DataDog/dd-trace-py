"""
ddtrace works with Gunicorn.

.. note::
    If you cannot wrap your Gunicorn server with the ``ddtrace-run`` command and
    it uses ``gevent`` workers be sure to ``import ddtrace.auto`` as early as
    possible in your application's lifecycle.
    Do not use ``ddtrace-run`` with ``import ddtrace.auto``.
"""
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate


deprecate(
    ("%s is deprecated" % (__name__)),
    message="Avoid using this package directly. "
    "Use ``ddtrace.auto`` or the ``ddtrace-run`` command to enable and configure this integration.",
    category=DDTraceDeprecationWarning,
    removal_version="3.0.0",
)


def get_version():
    # type: () -> str
    return ""


def patch():
    pass


def unpatch():
    pass
