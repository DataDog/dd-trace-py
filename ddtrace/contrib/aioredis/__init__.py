"""
The aioredis integration instruments aioredis requests. Version 1.3 and above are fully
supported.


Enabling
~~~~~~~~

The aioredis integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch() <ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(aioredis=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.aioredis["service"]

   The service name reported by default for aioredis instances.

   This option can also be set with the ``DD_AIOREDIS_SERVICE`` environment
   variable.

   Default: ``"redis"``

.. py:data:: ddtrace.config.aioredis["cmd_max_length"]

   Max allowable size for the aioredis command span tag.
   Anything beyond the max length will be replaced with ``"..."``.

   This option can also be set with the ``DD_AIOREDIS_CMD_MAX_LENGTH`` environment
   variable.

   Default: ``1000``

.. py:data:: ddtrace.config.aioedis["resource_only_command"]

   The span resource will only include the command executed. To include all
   arguments in the span resource, set this value to ``False``.

   This option can also be set with the ``DD_REDIS_RESOURCE_ONLY_COMMAND`` environment
   variable.

   Default: ``True``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the aioredis integration on a per-instance basis use the
``Pin`` API::

    import aioredis
    from ddtrace.trace import Pin

    myaioredis = aioredis.Aioredis()
    Pin.override(myaioredis, service="myaioredis")
"""
import warnings as _w  # noqa: E402


with _w.catch_warnings():
    _w.simplefilter("ignore", DeprecationWarning)
    # Required to allow users to import from  `ddtrace.contrib.aioredis.patch` directly
    from . import patch as _  # noqa: I001,F401

from ddtrace.contrib.internal.aioredis.patch import get_version  # noqa: F401
from ddtrace.contrib.internal.aioredis.patch import patch  # noqa: F401
from ddtrace.contrib.internal.aioredis.patch import unpatch  # noqa: F401
