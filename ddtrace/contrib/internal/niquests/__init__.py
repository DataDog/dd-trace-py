"""
The niquests__ integration traces HTTP requests made with the ``niquests``
library.


Enabling
~~~~~~~~

The ``niquests`` integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Alternatively, use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch

    patch(niquests=True)


Configuration
~~~~~~~~~~~~~

Use the following environment variables to configure the integration:

``DD_NIQUESTS_SERVICE``
   The service name for ``niquests`` requests. Defaults to ``"niquests"`` under
   the legacy span attribute schema and otherwise inherits the application
   service name.

``DD_NIQUESTS_DISTRIBUTED_TRACING``
   Whether to inject distributed tracing headers into requests. Defaults to
   ``True``.

``DD_NIQUESTS_SPLIT_BY_DOMAIN``
   Whether to use the request domain as the service name. Defaults to
   ``False``.

:ref:`Headers tracing <http-headers-tracing>` is supported for this integration.

:ref:`HTTP Tagging <http-tagging>` is supported for this integration.

.. __: https://niquests.readthedocs.io/
"""

from .patch import get_version
from .patch import patch
from .patch import unpatch


__all__ = ["patch", "unpatch", "get_version"]
