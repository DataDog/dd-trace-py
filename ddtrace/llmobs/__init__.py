"""
LLM Observability Service.
This is normally started automatically by including ``DD_LLMOBS_ENABLED=1 ddtrace-run <application_startup_command>``.
To start the service manually, invoke the ``enable`` method::
    from ddtrace.llmobs import LLMObs
    LLMObs.enable()
"""

from ._llmobs import LLMObs
from ._llmobs import LLMObsSpan


__all__ = ["LLMObs", "LLMObsSpan"]
