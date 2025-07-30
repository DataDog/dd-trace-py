"""
The vLLM integration instruments the vLLM Python library to trace LLM inference requests made through
both offline and online (serving) vLLM engines.

All traces submitted from the vLLM integration are tagged by:

- ``service``, ``env``, ``version``: see the `Unified Service Tagging docs <https://docs.datadoghq.com/getting_started/tagging/unified_service_tagging>`_.
- model used in the request.
- provider (typically "vllm" or extracted from model path).


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
    from ddtrace.trace import Pin

    # For offline LLM instances
    llm = vllm.LLM(model="facebook/opt-125m")
    Pin.override(llm, service="my-vllm-service")

    # For async engines
    engine = vllm.AsyncLLMEngine.from_engine_args(...)
    Pin.override(engine, service="my-vllm-service")
""" 