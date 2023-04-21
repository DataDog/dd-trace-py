"""
The OpenAI integration instruments the OpenAI Python library to emit metrics,
traces and logs for requests made to the OpenAI completions, chat completions
and embeddings endpoints.

All data submitted from the OpenAI integration is tagged with

- ``service`` / ``env`` / ``version``: See https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging/
- ``endpoint``: The OpenAI API endpoint used in the request.
- ``model``: The OpenAI model used in the request.
- ``organization.name``: The OpenAI organization used in the request.
- ``organization.id``: The OpenAI organization used in the request (when available).


Metrics
~~~~~~~

The following metrics are by default collected by the OpenAI integration.
Metrics can be disabled through the ``DD_OPENAI_METRICS_ENABLED`` environment variable (see below for more information).

.. important::

     DogStatsd has to be enabled in the agent (https://docs.datadoghq.com/developers/dogstatsd/?tab=hostagent).
     Use ``DD_DOGSTATSD_URL`` to specify the agent hostname and port.


..py:data:: request.error (count)

..py:data:: request.duration (distribution)

..py:data:: ratelimit.requests (gauge)

..py:data:: ratelimit.tokens (gauge)

..py:data:: ratelimit.remaining.requests (gauge)

..py:data:: ratelimit.remaining.tokens (gauge)

..py:data:: tokens.prompt (distribution)

..py:data:: tokens.completion (distribution)

..py:data:: tokens.total (distribution)



Prompt and Completion Sampling (beta)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Prompts and completions on Completion and ChatCompletion requests are always
sampled on span data. The length of the samples is limited by the ``truncation_threshold``
setting.

Logs are **not** emitted by default, see below for instructions to enable logs. When logs are enabled they are sampled at 10%.

.. important::

     ``DD_API_KEY`` environment variable is required to submit logs.


Enabling
~~~~~~~~

The OpenAI integration is enabled automatically when using
:ref:`ddtrace-run <ddtracerun>` or :func:`patch_all() <ddtrace.patch_all>`.

Note that these commands will also enable the ``requests`` and ``aiohttp``
integrations which will trace HTTP requests from the OpenAI library.

Or use :func:`patch() <ddtrace.patch>` to manually enable the OpenAI integration::

    from ddtrace import config, patch

    # Note: be sure to configure the integration before calling ``patch()``!
    # eg. config.openai["logs_enabled"] = True

    patch(openai=True)

    # if doing synchronous requests (the default)
    # patch(openai=True, requests=True)

    # or if doing asynchronous requests
    # patch(openai=True, aiohttp=True)

    # to disable requests or aiohttp integrations
    # patch(openai=True, requests=False)
    # patch(openai=True, aiohttp=False)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.openai["service"]

   The service name reported by default for OpenAI requests.

   This option can also be set with the ``DD_OPENAI_SERVICE`` environment
   variable.

   Default: ``DD_SERVICE``


.. py:data:: ddtrace.config.openai["logs_enabled"]

   Enable collection of prompts and completions as logs. The number of prompt/completions collected
   can be adjusted using the sampling option below.

   This option can also be set with the ``DD_OPENAI_LOGS_ENABLED`` environment
   variable.

   Note that the ``DD_API_KEY`` environment variable must be set to enable logs transmission.

   Default: ``False``


.. py:data:: ddtrace.config.openai["metrics_enabled"]

   Enable collection of OpenAI metrics.

   Note that the statsd port of the Datadog Agent must be enabled. See
   https://docs.datadoghq.com/developers/dogstatsd/?tab=hostagent#agent for
   instructions.

   This option can also be set with the ``DD_OPENAI_METRICS_ENABLED`` environment
   variable.

   Default: ``True``


.. py:data:: (beta) ddtrace.config.openai["truncation_threshold"]

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

    config.openai["metrics_enabled"] = False
    config.openai["logs_enabled"] = True
"""
from ...internal.utils.importlib import require_modules


required_modules = ["openai"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from . import patch as _patch

        patch = _patch.patch
        unpatch = _patch.unpatch

        __all__ = ["patch", "unpatch"]
