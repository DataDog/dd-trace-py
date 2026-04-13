"""
The LlamaIndex integration instruments the ``llama-index`` Python library to trace
LLM calls (chat, completion, streaming), query engines, retrievers, embeddings,
and agent workflows.

All traced operations emit spans with model names, token usage, and input/output
data. When LLM Observability is enabled, spans are automatically enriched with
structured input/output messages and metrics.

Enabling
~~~~~~~~

The LlamaIndex integration is enabled automatically when you use
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.
"""
