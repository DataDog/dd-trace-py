"""
The vLLM integration traces requests through the vLLM V1 engine.

**Note**: This integration **only supports vLLM V1** (VLLM_USE_V1=1). V0 engine support has been
removed as V0 is deprecated and will be removed in a future vLLM release.


Enabling
~~~~~~~~

The vLLM integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :func:`patch_all()<ddtrace.patch_all>`.

Alternatively, use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(vllm=True)


Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.vllm["service"]

   The service name reported by default for vLLM requests.

   This option can also be set with the ``DD_VLLM_SERVICE`` environment variable.

   Default: ``"vllm"``


Architecture
~~~~~~~~~~~~

The integration uses **engine-side tracing** to capture all requests regardless of API entry point:

1. **Model Name Injection** (``LLMEngine.__init__`` / ``AsyncLLM.__init__``):
   - Extracts and stores model name for span tagging
   - Forces ``log_stats=True`` to enable latency and token metrics collection

2. **Context Injection** (``Processor.process_inputs``):
   - Injects Datadog trace context into ``trace_headers``
   - Context propagates through the engine automatically

3. **Span Creation** (``OutputProcessor.process_outputs``):
   - Creates spans when requests finish
   - Extracts data from ``RequestState`` and ``EngineCoreOutput``
   - Decodes prompt from token IDs for chat requests when text is unavailable
   - Works for all operations: completion, chat, embedding, cross-encoding

This design ensures:
- All requests are traced (AsyncLLM, LLM, API server, chat)
- Complete timing and token metrics from engine stats
- Full prompt text capture (including chat conversations)
- Minimal performance overhead


Span Tags
~~~~~~~~~

All spans are tagged with:

**Request Information**:
- ``vllm.request.model``: Model name
- ``vllm.request.provider``: ``"vllm"``

**Latency Metrics**:
- ``vllm.latency.ttft``: Time to first token (seconds)
- ``vllm.latency.queue``: Queue wait time (seconds)
- ``vllm.latency.prefill``: Prefill phase time (seconds)
- ``vllm.latency.decode``: Decode phase time (seconds)
- ``vllm.latency.inference``: Total inference time (seconds)

**LLMObs Tags** (when LLMObs is enabled):

For completion/chat operations:
- ``input_messages``: Prompt text (auto-decoded for chat requests)
- ``output_messages``: Generated text
- ``input_tokens``: Number of input tokens
- ``output_tokens``: Number of generated tokens
- ``temperature``, ``max_tokens``, ``top_p``, ``n``: Sampling parameters
- ``num_cached_tokens``: Number of KV cache hits

For embedding operations:
- ``input_documents``: Input text or token IDs
- ``output_value``: Embedding shape description
- ``embedding_dim``: Embedding dimension
- ``num_embeddings``: Number of embeddings returned


Supported Operations
~~~~~~~~~~~~~~~~~~~~

**Async Streaming** (``AsyncLLM``):
- ``generate()``: Text completion
- ``encode()``: Text embedding

**Offline Batch** (``LLM``):
- ``generate()``: Text completion
- ``chat()``: Multi-turn conversations
- ``encode()``: Text embedding
- ``_cross_encoding_score()``: Cross-encoding scores

**API Server**:
- All OpenAI-compatible endpoints (automatically traced through engine)


Requirements
~~~~~~~~~~~~

- vLLM V1 (``VLLM_USE_V1=1``)
- vLLM >= 0.10.2 (for V1 trace header propagation support)
"""
