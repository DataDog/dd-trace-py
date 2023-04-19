"""
The OpenAI integration instruments the OpenAI Python library to emit metrics,
traces and logs for requests made to the OpenAI completions, chat completions
and embeddings endpoints.

By enabling the requests (or aiohttp, if using async) integrations the traces
from this integration will include the HTTP requests from the OpenAI library.


Prompt and Completion Sampling
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Prompts and completions on Completion and ChatCompletion requests are always
sampled on span data. The length of the samples is limited by the ``truncation_threshold``
setting.

Logs are **not** emitted by default. When logs are enabled they are sampled at 10%.


.. important::

     ``DD_API_KEY`` is required to submit logs.



Enabling
~~~~~~~~

The OpenAI integration is enabled automatically when using
:ref:`ddtrace-run <ddtracerun>` or :func:`patch_all() <ddtrace.patch_all>`.

Or use :func:`patch() <ddtrace.patch>` to manually enable the integration::

    from ddtrace import config, patch

    # Note: be sure to configure the integration before calling ``patch()``!
    # eg. config.openai.logs_enabled = True

    patch(openai=True)

    # or if doing synchronous requests (the default)
    patch(openai=True, requests=True)

    # or if doing asynchronous requests
    # patch(openai=True, aiohttp=True)



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

   Note that ``DD_API_KEY`` must be set to enable logs transmission.

   Default: ``False``


.. py:data:: ddtrace.config.openai["metrics_enabled"]

   Enable collection of OpenAI metrics.

   Note that the statsd port of the Datadog Agent must be enabled. See
   https://docs.datadoghq.com/developers/dogstatsd/?tab=hostagent#agent for
   instructions.

   This option can also be set with the ``DD_OPENAI_METRICS_ENABLED`` environment
   variable.

   Default: ``True``


.. py:data:: ddtrace.config.openai["truncation_threshold"]

   Configure the maximum number of characters for prompts and completions within span tags.

   Text exceeding the maximum number of characters will be truncated to the character limit
   and have ``...`` appended to the end.

   This option can also be set with the ``DD_OPENAI_TRUNCATION_THRESHOLD`` environment
   variable.

   Default: ``512``


.. py:data:: (beta) ddtrace.config.openai["span_prompt_completion_sample_rate"]

   Configure the sample rate for the collection of prompts and completions as span tags.

   This option can also be set with the ``DD_OPENAI_SPAN_PROMPT_COMPLETION_SAMPLE_RATE`` environment
   variable.

   Default: ``1.0``


.. py:data:: (beta) ddtrace.config.openai["log_prompt_completion_sample_rate"]

   Configure the sample rate for the collection of prompts and completions as logs.

   This option can also be set with the ``DD_OPENAI_LOG_PROMPT_COMPLETION_SAMPLE_RATE`` environment
   variable.

   Default: ``0.1``



Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the OpenAI integration on a per-instance basis use the
``Pin`` API::

    import openai
    from ddtrace import Pin, config

    Pin.override(openai, service="my-openai-service")

    config.openai.metrics_enabled = False
    config.openai.logs_enabled = True
"""
from ...internal.utils.importlib import require_modules


required_modules = ["openai"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch
        from .patch import unpatch

        __all__ = ["patch", "unpatch"]
