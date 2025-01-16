"""
Trace the standard library ``httplib``/``http.client`` libraries to trace
HTTP requests.


Enabling
~~~~~~~~

The httplib integration is disabled by default. It can be enabled when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`
using the ``DD_TRACE_HTTPLIB_ENABLED`` environment variable::

    DD_TRACE_HTTPLIB_ENABLED=true ddtrace-run ....

The integration can also be enabled manually in code with
:func:`patch_all()<ddtrace.patch_all>`::

    from ddtrace import patch_all
    patch_all(httplib=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.httplib['distributed_tracing']

   Include distributed tracing headers in requests sent from httplib.

   This option can also be set with the ``DD_HTTPLIB_DISTRIBUTED_TRACING``
   environment variable.

   Default: ``True``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~


The integration can be configured per instance::

    from ddtrace import config

    # Disable distributed tracing globally.
    config.httplib['distributed_tracing'] = False

    # Change the service distributed tracing option only for this HTTP
    # connection.

    # Python 2
    connection = urllib.HTTPConnection('www.datadog.com')

    # Python 3
    connection = http.client.HTTPConnection('www.datadog.com')

    cfg = config.get_from(connection)
    cfg['distributed_tracing'] = True


:ref:`Headers tracing <http-headers-tracing>` is supported for this integration.
"""


import warnings as _w


with _w.catch_warnings():
    _w.simplefilter("ignore", DeprecationWarning)
    from . import patch as _  # noqa: F401, I001

from ddtrace.contrib.internal.httplib.patch import get_version  # noqa: F401
from ddtrace.contrib.internal.httplib.patch import patch  # noqa: F401
from ddtrace.contrib.internal.httplib.patch import unpatch  # noqa: F401
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate


deprecate(
    ("%s is deprecated" % (__name__)),
    message="Avoid using this package directly. "
    "Set DD_TRACE_HTTPLIB_ENABLED=true and use ``ddtrace.auto`` or the "
    "``ddtrace-run`` command to enable and configure this integration.",
    category=DDTraceDeprecationWarning,
    removal_version="3.0.0",
)
