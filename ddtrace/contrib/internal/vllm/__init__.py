"""
The vLLM integration instruments the vLLM Python library to trace requests made to models for completions,
embeddings, and cross-encoding operations.

The integration supports both vLLM V0 (engine-side tracing) and V1 (client-side tracing) engine modes,
automatically detecting and adapting to the active engine version.

All traces submitted from the vLLM integration are tagged by:

- ``service``, ``env``, ``version``: see the `Unified Service Tagging docs <https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging>`_.
- ``vllm.request.model``: vLLM model used in the request.
- ``vllm.request.provider``: Provider name (always "vllm").

Request latency metrics (V0 engine only):

- ``vllm.latency.ttft``: Time to first token (TTFT) in seconds - total time from request arrival
  to first token generation.
- ``vllm.latency.queue``: Time spent in queue (waiting phase) in seconds.
- ``vllm.latency.prefill``: Time spent in prefill phase in seconds - from scheduling to first
  token.
- ``vllm.latency.decode``: Time spent in decode phase in seconds - from first token to last token.
- ``vllm.latency.inference``: Total time spent in inference (running phase) in seconds - from
  scheduling to completion.
- ``vllm.latency.model_forward``: Time spent in model forward pass in seconds (when available).
- ``vllm.latency.model_execute``: Time spent in model execution in seconds - includes forward,
  sync, and sampling (when available).


Supported Operations
~~~~~~~~~~~~~~~~~~~~

The integration traces the following vLLM operations:

- **Completion**: Text generation using ``LLM.generate()`` (offline) or ``AsyncLLM.generate()`` (async).
- **Embedding**: Vector embeddings using ``LLM.encode()`` (offline) or ``AsyncLLM.encode()`` (async).
- **Cross-encoding**: Similarity scoring using ``LLM._cross_encoding_score()``.


Engine Version Support
~~~~~~~~~~~~~~~~~~~~~~

The integration automatically detects the active vLLM engine version (V0 or V1) via the ``VLLM_USE_V1``
environment variable and adapts its tracing strategy:

- **V0 Engine**: Traces at the engine level (``LLMEngine._process_model_outputs``), with trace context
  propagation via injected headers.
- **V1 Engine**: Traces at the client level (``AsyncLLM.generate``, ``AsyncLLM.encode``, etc.), with
  direct parent-child span relationships.


Enabling
~~~~~~~~

The vLLM integration is enabled automatically when you use
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Alternatively, use :func:`patch() <ddtrace.patch>` to manually enable the vLLM integration::

    from ddtrace import config, patch

    patch(vllm=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.vllm["service"]

   The service name reported by default for vLLM requests.

   Alternatively, you can set this option with the ``DD_SERVICE`` or ``DD_VLLM_SERVICE`` environment
   variables.

   Default: ``DD_SERVICE``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the vLLM integration on a per-instance basis use the
``Pin`` API::

    import vllm
    from ddtrace import config
    from ddtrace.trace import Pin

    Pin.override(vllm, service="my-vllm-service")
"""
