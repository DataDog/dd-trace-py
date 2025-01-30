"""
The LangChain OpenAI integration instruments the LangChain OpenAI Python partner library to emit traces for the embeddings interface.

All traces submitted from the LangChain integration are tagged by:

- ``service``, ``env``, ``version``: see the `Unified Service Tagging docs <https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging>`_.
- ``langchain.request.provider``: LLM provider used in the request.
- ``langchain.request.model``: LLM/Chat/Embeddings model used in the request.
- ``langchain.request.api_key``: LLM provider API key used to make the request (obfuscated into the format ``...XXXX`` where ``XXXX`` is the last 4 digits of the key).


(beta) Prompt and Completion Sampling
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The following data is collected in span tags with a default sampling rate of ``1.0``:

- Prompt inputs and completions for the ``LLM`` interface.
- Message inputs and completions for the ``ChatModel`` interface.
- Embedding inputs for the ``Embeddings`` interface.
- Prompt inputs, chain inputs, and outputs for the ``Chain`` interface.
- Query inputs and document outputs for the ``VectorStore`` interface.

Prompt and message inputs and completions can also be emitted as log data.
Logs are **not** emitted by default. When logs are enabled they are sampled at ``0.1``.

Read the **Global Configuration** section for information about enabling logs and configuring sampling
rates.

.. important::

    To submit logs, you must set the ``DD_API_KEY`` environment variable.

    Set ``DD_SITE`` to send logs to a Datadog site such as ``datadoghq.eu``. The default is ``datadoghq.com``.


Enabling
~~~~~~~~

The LangChain OpenAI integration is enabled automatically when you use
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Note that these commands also enable the ``requests`` and ``aiohttp``
integrations which trace HTTP requests to LLM providers, as well as the
``openai`` integration which traces requests to the OpenAI library.

Alternatively, use :func:`patch() <ddtrace.patch>` to manually enable the LangChain integration::
    from ddtrace import config, patch

    patch(langchain_openai=True)

    # to trace synchronous HTTP requests
    # patch(langchain_openai=True, requests=True)

    # to trace asynchronous HTTP requests (to the OpenAI library)
    # patch(langchain_openai=True, aiohttp=True)

    # to include underlying OpenAI spans from the OpenAI integration
    # patch(langchain_openai=True, openai=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.langchain["service"]

   The service name reported by default for LangChain requests.

   Alternatively, you can set this option with the ``DD_SERVICE`` or ``DD_LANGCHAIN_SERVICE`` environment
   variables.

   Default: ``DD_SERVICE``


.. py:data:: (beta) ddtrace.config.langchain["span_char_limit"]

   Configure the maximum number of characters for the following data within span tags:

   - Prompt inputs and completions
   - Message inputs and completions
   - Embedding inputs

   Text exceeding the maximum number of characters is truncated to the character limit
   and has ``...`` appended to the end.

   Alternatively, you can set this option with the ``DD_LANGCHAIN_SPAN_CHAR_LIMIT`` environment
   variable.

   Default: ``128``


.. py:data:: (beta) ddtrace.config.langchain["span_prompt_completion_sample_rate"]

   Configure the sample rate for the collection of prompts and completions as span tags.

   Alternatively, you can set this option with the ``DD_LANGCHAIN_SPAN_PROMPT_COMPLETION_SAMPLE_RATE`` environment
   variable.

   Default: ``1.0``

"""  # noqa: E501

# Required to allow users to import from  `ddtrace.contrib.langchain.patch` directly
import warnings as _w


with _w.catch_warnings():
    _w.simplefilter("ignore", DeprecationWarning)
    from . import patch as _  # noqa: F401, I001


from ddtrace.contrib.internal.langchain_openai.patch import get_version  # noqa: F401
from ddtrace.contrib.internal.langchain_openai.patch import patch  # noqa: F401
from ddtrace.contrib.internal.langchain_openai.patch import unpatch  # noqa: F401
