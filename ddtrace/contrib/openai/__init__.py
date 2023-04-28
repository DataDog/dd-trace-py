"""
The OpenAI integration instruments the OpenAI Python library to emit metrics,
traces, and logs (logs are disabled by default) for requests made to the
completions, chat completions, and embeddings endpoints.

All metrics, logs, and traces submitted from the OpenAI integration are tagged with:

- ``service``, ``env``, ``version``: see the `Unified Service Tagging docs <https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging>`_.
- ``endpoint``: OpenAI API endpoint used in the request.
- ``model``: OpenAI model used in the request.
- ``organization.name``: OpenAI organization name used in the request.
- ``organization.id``: OpenAI organization ID used in the request (when available).


Metrics
~~~~~~~

The following metrics are collected by default by the OpenAI integration.

.. important::
    If the Agent is configured to use a non-default Statsd hostname or port, use ``DD_DOGSTATSD_URL`` to configure
    ``ddtrace`` to use it.


.. important::
   Metrics only reflect usage of the supported completions, chat completions, and embedding endpoints. Usage of other
   OpenAI endpoints will not be recorded.


.. py:data:: openai.request.duration

   Type: ``distribution``


.. py:data:: openai.request.error

   Type: ``count``


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


(beta) Prompt and Completion Sampling
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The following data is collected in span tags with a default sampling rate of ``1.0``:

- Prompt inputs and completions for the ``completions`` endpoint.
- Message inputs and completions for the ``chat.completions`` endpoint.
- Embedding inputs for the ``embeddings`` endpoint.

Prompt and message inputs and completions can also be emitted as log data.
Logs are **not** emitted by default. When logs are enabled they are sampled at ``0.1``.

Read the **Global Configuration** section for information about enabling logs and configuring sampling
rates.

.. important::

    To submit logs, you must set the ``DD_API_KEY`` environment variable.

    Set ``DD_SITE`` to send logs to a Datadog site such as ``datadoghq.eu``. The default is ``datadoghq.com``.


(beta) Streamed Responses Support
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The OpenAI integration **estimates** prompt and completion token counts if streaming is turned on.
This is because the ``usage`` field is no longer returned in streamed completions, which is what
the integration relies on for reporting metrics.

Streaming responses should produce a ``openai.stream`` span. This span is tagged with estimated
completion and total tokens. The integration will make a best effort attempt to tag the original
parent ``openai.request`` span with completion and total usage information, but this parent span
may be flushed before this information is available.

The ``_est_tokens`` function implements token count estimations. It returns the average of simple
token estimation techniques that do not rely on installing a tokenizer.


Enabling
~~~~~~~~

The OpenAI integration is enabled automatically when you use
:ref:`ddtrace-run <ddtracerun>` or :func:`patch_all() <ddtrace.patch_all>`.

Note that these commands also enable the ``requests`` and ``aiohttp``
integrations which trace HTTP requests from the OpenAI library.

Alternatively, use :func:`patch() <ddtrace.patch>` to manually enable the OpenAI integration::

    from ddtrace import config, patch

    # Note: be sure to configure the integration before calling ``patch()``!
    # eg. config.openai["logs_enabled"] = True

    patch(openai=True)

    # to trace synchronous HTTP requests from the OpenAI library
    # patch(openai=True, requests=True)

    # to trace asynchronous HTTP requests from the OpenAI library
    # patch(openai=True, aiohttp=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.openai["service"]

   The service name reported by default for OpenAI requests.

   Alternatively, you can set this option with the ``DD_SERVICE`` or ``DD_OPENAI_SERVICE`` environment
   variables.

   Default: ``DD_SERVICE``


.. py:data:: ddtrace.config.openai["logs_enabled"]

   Enable collection of prompts and completions as logs. You can adjust the rate of prompts and completions collected
   using the sample rate configuration described below.

   Alternatively, you can set this option with the ``DD_OPENAI_LOGS_ENABLED`` environment
   variable.

   Note that you must set the ``DD_API_KEY`` environment variable to enable sending logs.

   Default: ``False``


.. py:data:: ddtrace.config.openai["metrics_enabled"]

   Enable collection of OpenAI metrics.

   If the Datadog Agent is configured to use a non-default Statsd hostname
   or port, use ``DD_DOGSTATSD_URL`` to configure ``ddtrace`` to use it.

   Alternatively, you can set this option with the ``DD_OPENAI_METRICS_ENABLED`` environment
   variable.

   Default: ``True``


.. py:data:: (beta) ddtrace.config.openai["span_char_limit"]

   Configure the maximum number of characters for the following data within span tags:

   - Prompt inputs and completions
   - Message inputs and completions
   - Embedding inputs

   Text exceeding the maximum number of characters is truncated to the character limit
   and has ``...`` appended to the end.

   Alternatively, you can set this option with the ``DD_OPENAI_SPAN_CHAR_LIMIT`` environment
   variable.

   Default: ``128``


.. py:data:: (beta) ddtrace.config.openai["span_prompt_completion_sample_rate"]

   Configure the sample rate for the collection of prompts and completions as span tags.

   Alternatively, you can set this option with the ``DD_OPENAI_SPAN_PROMPT_COMPLETION_SAMPLE_RATE`` environment
   variable.

   Default: ``1.0``


.. py:data:: (beta) ddtrace.config.openai["log_prompt_completion_sample_rate"]

   Configure the sample rate for the collection of prompts and completions as logs.

   Alternatively, you can set this option with the ``DD_OPENAI_LOG_PROMPT_COMPLETION_SAMPLE_RATE`` environment
   variable.

   Default: ``0.1``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the OpenAI integration on a per-instance basis use the
``Pin`` API::

    import openai
    from ddtrace import Pin, config

    Pin.override(openai, service="my-openai-service")
"""  # noqa: E501
from ...internal.utils.importlib import require_modules


required_modules = ["openai"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from . import patch as _patch

        patch = _patch.patch
        unpatch = _patch.unpatch

        __all__ = ["patch", "unpatch"]
