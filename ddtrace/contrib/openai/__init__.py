"""
The OpenAI integration instruments the OpenAI Python library.


Enabling
~~~~~~~~

The OpenAI integration is enabled automatically when using
:ref:`ddtrace-run <ddtracerun>` or :func:`patch_all() <ddtrace.patch_all>`.

Or use :func:`patch() <ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(openai=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.openai["service"]

   The service name reported by default for OpenAI requests.

   This option can also be set with the ``DD_OPENAI_SERVICE`` environment
   variable.

   Default: ``"openai"``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the OpenAI integration on an per-instance basis use the
``Pin`` API::

    import openai
    from ddtrace import Pin

    Pin.override(openai, service="my-openai-service")
"""
from ...internal.utils.importlib import require_modules


required_modules = ["openai"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch
        from .patch import unpatch

        __all__ = ["patch", "unpatch"]
