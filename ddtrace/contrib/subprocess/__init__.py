"""
The subprocess integration will add tracing to all subprocess executions
started in your application. It will be automatically enabled if Application
Security is enabled with::

    DD_APPSEC_ENABLED=true


Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.subprocess['sensitive_wildcards']

   Comma separated list of fnmatch-style wildcards Subprocess parameters matching these
   wildcards will be scrubbed and replaced by a "?".

   Default: ``None`` for the config value but note that there are some wildcards always
   enabled in this integration that you can check on
   ```ddtrace.contrib.subprocess.constants.SENSITIVE_WORDS_WILDCARDS```.
"""


# Required to allow users to import from  `ddtrace.contrib.subprocess.patch` directly
import warnings as _w


with _w.catch_warnings():
    _w.simplefilter("ignore", DeprecationWarning)
    from . import patch as _  # noqa: F401, I001

# Expose public methods
from ddtrace.contrib.internal.subprocess.patch import get_version  # noqa: F401
from ddtrace.contrib.internal.subprocess.patch import patch  # noqa: F401
from ddtrace.contrib.internal.subprocess.patch import unpatch  # noqa: F401
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate


deprecate(
    ("%s is deprecated" % (__name__)),
    message="Avoid using this package directly. "
    "Use ``ddtrace.auto`` or the ``ddtrace-run`` command to enable and configure this integration.",
    category=DDTraceDeprecationWarning,
    removal_version="3.0.0",
)
