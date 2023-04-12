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


.. py:data:: ddtrace.config.openai["logs_enabled"]

   Enable collection of prompts and completions as logs. The number of prompt/completions collected
   can be adjusted using the sampling option below.

   This option can also be set with the ``DD_OPENAI_LOGS_ENABLED`` environment
   variable.

   Note that DD_API_KEY must be set.

   Default: ``False``


.. py:data:: ddtrace.config.openai["prompt_completion_sample_rate"]

   Configure the sample rate for the collection of prompts and completions as logs.

   This option can also be set with the ``DD_OPENAI_PROMPT_COMPLETION_SAMPLE_RATE`` environment
   variable.

   Default: ``1.0``


.. py:data:: ddtrace.config.openai["metrics_enabled"]

   Enable collection of OpenAI metrics.

   Note that the statsd port of the Datadog Agent must be enabled. See
   https://docs.datadoghq.com/developers/dogstatsd/?tab=hostagent#agent for
   instructions.

   This option can also be set with the ``DD_OPENAI_METRICS_ENABLED`` environment
   variable.

   Default: ``True``


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
