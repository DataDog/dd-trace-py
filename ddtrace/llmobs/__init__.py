"""
LLM Observability Service.
This is normally started automatically by including ``DD_LLMOBS_ENABLED=1 ddtrace-run <application_startup_command>``.
To start the service manually, invoke the ``enable`` method::
    from ddtrace.llmobs import LLMObs
    LLMObs.enable()
"""

from ddtrace.llmobs._evaluators import BaseEvaluator
from ddtrace.llmobs._evaluators import BaseSummaryEvaluator
from ddtrace.llmobs._evaluators import EvaluatorContext
from ddtrace.llmobs._evaluators import SummaryEvaluatorContext
from ddtrace.llmobs._experiment import Dataset
from ddtrace.llmobs._experiment import DatasetRecord
from ddtrace.llmobs._experiment import EvaluatorResult
from ddtrace.llmobs._llmobs import LLMObs
from ddtrace.llmobs._llmobs import LLMObsSpan
from ddtrace.llmobs.types import Prompt


__all__ = [
    "LLMObs",
    "LLMObsSpan",
    "Dataset",
    "DatasetRecord",
    "Prompt",
    "BaseEvaluator",
    "BaseSummaryEvaluator",
    "EvaluatorContext",
    "EvaluatorResult",
    "SummaryEvaluatorContext",
]
