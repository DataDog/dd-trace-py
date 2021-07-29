"""
The httpx__ integration traces all HTTP requests made with the ``httpx``
library.

Enabling
~~~~~~~~

The ``httpx`` integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`patch_all()<patch_all>`.

Alternatively, use :ref:`patch()<patch>` to manually enable the integration::

    from ddtrace import patch
    patch(httpx=True)

    # use httpx like usual


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.httpx['service']

   The default service name for ``httpx`` requests. This value will
   be overridden by an instance override or if the ``split_by_domain`` setting is
   enabled.

   This option can also be set with the ``DD_HTTPX_SERVICE`` environment
   variable.

   Default: ``None`` (inherit from ``DD_SERVICE``)


.. py:data:: ddtrace.config.httpx['distributed_tracing']

   Whether or not to inject distributed tracing headers into requests.

   Default: ``True``


.. py:data:: ddtrace.config.httpx['split_by_domain']

   Whether or not to use the domain name of requests as the service name. This
   setting can be overridden with session overrides (described in the Instance
   Configuration section).

   Default: ``False``


:ref:`Headers tracing <http-headers-tracing>` is supported for this integration.

:ref:`HTTP Tagging <http-tagging>` is supported for this integration.


.. __: https://www.python-httpx.org/
"""
from ...utils.importlib import require_modules


required_modules = ["httpx"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch
        from .patch import unpatch

        __all__ = [
            "patch",
            "unpatch",
        ]
