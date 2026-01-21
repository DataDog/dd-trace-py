"""
LLM Observability Service.
This is normally started automatically by including ``DD_LLMOBS_ENABLED=1 ddtrace-run <application_startup_command>``.
To start the service manually, invoke the ``enable`` method::
    from ddtrace.llmobs import LLMObs
    LLMObs.enable()
"""

from ._evaluators import BaseEvaluator
from ._evaluators import EvaluatorContext
from ._evaluators import JSONValidator
from ._evaluators import LengthValidator
from ._evaluators import RegexMatch
from ._evaluators import SemanticSimilarity
from ._evaluators import StringCheck
from ._experiment import Dataset
from ._experiment import DatasetRecord
from ._experiment import EvaluatorResult
from ._llmobs import LLMObs
from ._llmobs import LLMObsSpan
from .types import Prompt


__all__ = [
    "LLMObs",
    "LLMObsSpan",
    "Dataset",
    "DatasetRecord",
    "Prompt",
    "BaseEvaluator",
    "EvaluatorContext",
    "StringCheck",
    "RegexMatch",
    "LengthValidator",
    "JSONValidator",
    "SemanticSimilarity",
    "EvaluatorResult",
]
