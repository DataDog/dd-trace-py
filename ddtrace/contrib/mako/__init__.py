"""
The ``mako`` integration traces templates rendering.
Auto instrumentation is available using the ``patch``. The following is an example::

    from ddtrace import patch
    from mako.template import Template

    patch(mako=True)

    t = Template(filename="index.html")

"""


# Required to allow users to import from  `ddtrace.contrib.mako.patch` directly
import warnings as _w


with _w.catch_warnings():
    _w.simplefilter("ignore", DeprecationWarning)
    from . import patch as _  # noqa: F401, I001

# Expose public methods
from ddtrace.contrib.internal.mako.patch import get_version  # noqa: F401
from ddtrace.contrib.internal.mako.patch import patch  # noqa: F401
from ddtrace.contrib.internal.mako.patch import unpatch  # noqa: F401
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate


deprecate(
    ("%s is deprecated" % (__name__)),
    message="Avoid using this package directly. "
    "Use ``ddtrace.auto`` or the ``ddtrace-run`` command to enable and configure this integration.",
    category=DDTraceDeprecationWarning,
    removal_version="3.0.0",
)
