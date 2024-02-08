"""
LLM Observability Service.
This is normally started automatically by including ``DD_LLMOBS_ENABLED=1`` application startup command.
To start the service manually, invoke the ``enable`` method::
    from ddtrace.llmobs import LLMObs
    LLMObs.enable()
"""
from ._llmobs import LLMObs


__all__ = ["LLMObs"]
