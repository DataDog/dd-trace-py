"""
The OpenAI integration instruments the OpenAI Python library to emit metrics,
traces and logs (not enabled by default) for requests made to the OpenAI
completions, chat completions and embeddings endpoints.

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


.. py:data:: openai.request.error

   Type: ``count``


.. py:data:: openai.request.duration

   Type: ``distribution``


.. py:data:: openai.ratelimit.requests

   Type: ``gauge``


.. py:data:: openai.ratelimit.tokens

   Type: ``gauge``


.. py:data:: openai.ratelimit.remaining.requests

   Type: ``gauge``


.. py:data:: openai.ratelimit.remaining.tokens

   Type: ``gauge``


.. py:data:: openai.tokens.prompt

   Type: ``distribution``


.. py:data:: openai.tokens.completion

   Type: ``distribution``


.. py:data:: openai.tokens.total

   Type: ``distribution``


Prompt and Completion Sampling (beta)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The following data is collected in span tags with a default sampling rate of ``1.0``:

- Prompt inputs and completions for the ``completions`` endpoint
- Message inputs and completions for the ``chat.completions`` endpoint
- Embedding inputs for the ``embeddings`` endpoint

Prompt/message inputs and completions can also be emitted as log data.
Logs are **not** emitted by default. When logs are enabled they are sampled at ``0.1``.

See details in the **Global Configuration** section on how to enable logs and configure sampling
rates.

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

    # to trace synchronous HTTP requests from the OpenAI library
    # patch(openai=True, requests=True)

    # to trace asynchronous HTTP requests from the OpenAI library
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


.. py:data:: (beta) ddtrace.config.openai["span_char_limit"]

   Configure the maximum number of characters for the following data within span tags.:

   - Prompt inputs and completions
   - Message inputs and completions
   - Embedding inputs

   Text exceeding the maximum number of characters will be truncated to the character limit
   and have ``...`` appended to the end.

   This option can also be set with the ``DD_OPENAI_SPAN_CHAR_LIMIT`` environment
   variable.

   Default: ``128``


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
