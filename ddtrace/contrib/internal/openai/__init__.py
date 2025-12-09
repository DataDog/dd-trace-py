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


Streamed Responses Support
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The OpenAI integration **estimates** prompt and completion token counts for streamed completion/chat completion responses if
``stream_options["include_usage"]`` is set to ``False`` in the request. This is because the ``usage`` field is not returned
by default in streamed completion/chat completions, which is what the integration relies on for reporting token metrics.

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


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the OpenAI integration on a per-instance basis use the
``Pin`` API::

    import openai
    from ddtrace import config
    from ddtrace._trace.pin import Pin

    Pin.override(openai, service="my-openai-service")
"""  # noqa: E501
