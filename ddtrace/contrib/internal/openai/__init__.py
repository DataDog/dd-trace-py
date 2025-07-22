"""
The OpenAI integration instruments the OpenAI Python library to emit traces for requests made to the models,
completions, chat completions, images, embeddings, audio, files, and moderations endpoints.

All traces submitted from the OpenAI integration are tagged by:

- ``service``, ``env``, ``version``: see the `Unified Service Tagging docs <https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging>`_.
- ``openai.request.endpoint``: OpenAI API endpoint used in the request.
- ``openai.request.method``: HTTP method type used in the request.
- ``openai.request.model``: OpenAI model used in the request.
- ``openai.organization.name``: OpenAI organization name used in the request.
- ``openai.organization.id``: OpenAI organization ID used in the request (when available).
- ``openai.user.api_key``: OpenAI API key used to make the request (obfuscated to match the OpenAI UI representation ``sk-...XXXX`` where ``XXXX`` is the last 4 digits of the key).


(beta) Prompt and Completion Sampling
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The following data is collected in span tags with a default sampling rate of ``1.0``:

- Prompt inputs and completions for the ``completions`` endpoint.
- Message inputs and completions for the ``chat.completions`` endpoint.
- Embedding inputs for the ``embeddings`` endpoint.
- Image input filenames and completion URLs for the ``images`` endpoint.
- Audio input filenames and completions for the ``audio`` endpoint.


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
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

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


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the OpenAI integration on a per-instance basis use the
``Pin`` API::

    import openai
    from ddtrace import config
    from ddtrace.trace import Pin

    Pin.override(openai, service="my-openai-service")
"""  # noqa: E501
