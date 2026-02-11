"""
This integration instruments the ``llama-index`` library.

Enabling
~~~~~~~~

The llama_index integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch() <ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(llama_index=True)
    import llama_index
    ...

Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.llama_index["service"]

   The service name reported by default for llama_index spans.

   This option can also be set with the ``DD_LLAMA_INDEX_SERVICE`` environment
   variable.

   Default: ``"llama_index"``

"""
