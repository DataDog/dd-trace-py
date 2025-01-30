"""
The ``requests`` integration traces all HTTP requests made with the ``requests``
library.

The default service name used is `requests` but it can be configured to match
the services that the specific requests are made to.

Enabling
~~~~~~~~

The requests integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(requests=True)

    # use requests like usual


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.requests['service']

   The service name reported by default for requests queries. This value will
   be overridden by an instance override or if the split_by_domain setting is
   enabled.

   This option can also be set with the ``DD_REQUESTS_SERVICE`` environment
   variable.

   Default: ``"requests"``


    .. _requests-config-distributed-tracing:
.. py:data:: ddtrace.config.requests['distributed_tracing']

   Whether or not to parse distributed tracing headers.

   Default: ``True``


.. py:data:: ddtrace.config.requests['trace_query_string']

   Whether or not to include the query string as a tag.

   Default: ``False``


.. py:data:: ddtrace.config.requests['split_by_domain']

   Whether or not to use the domain name of requests as the service name. This
   setting can be overridden with session overrides (described in the Instance
   Configuration section).

   Default: ``False``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To set configuration options for all requests made with a ``requests.Session`` object
use the config API::

    from ddtrace import config
    from ddtrace.trace import Pin
    from requests import Session

    session = Session()
    Pin.override(session, service='auth-api')
"""


# Required to allow users to import from  `ddtrace.contrib.requests.patch` directly
import warnings as _w


with _w.catch_warnings():
    _w.simplefilter("ignore", DeprecationWarning)
    from . import patch as _  # noqa: F401, I001


from ddtrace.contrib.internal.requests.patch import get_version  # noqa: F401
from ddtrace.contrib.internal.requests.patch import patch  # noqa: F401
from ddtrace.contrib.internal.requests.patch import unpatch  # noqa: F401
from ddtrace.contrib.internal.requests.session import TracedSession
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate


def __getattr__(name):
    if name in ("patch", "unpatch", "get_version"):
        deprecate(
            ("%s.%s is deprecated" % (__name__, name)),
            message="Use ``import ddtrace.auto`` or the ``ddtrace-run`` command to configure this integration.",
            category=DDTraceDeprecationWarning,
            removal_version="3.0.0",
        )

    if name in globals():
        return globals()[name]
    raise AttributeError("%s has no attribute %s", __name__, name)


__all__ = ["TracedSession"]
