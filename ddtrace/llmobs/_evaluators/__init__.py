"""LLMObs evaluators module.

This module provides base classes and built-in evaluators for evaluating LLM tasks in the LLMObs SDK.
"""

from importlib import import_module
from typing import TYPE_CHECKING
from typing import Any


if TYPE_CHECKING:
    from ddtrace.llmobs._evaluators.format import JSONEvaluator
    from ddtrace.llmobs._evaluators.format import LengthEvaluator
    from ddtrace.llmobs._evaluators.llm_judge import BooleanStructuredOutput
    from ddtrace.llmobs._evaluators.llm_judge import CategoricalStructuredOutput
    from ddtrace.llmobs._evaluators.llm_judge import LLMJudge
    from ddtrace.llmobs._evaluators.llm_judge import ScoreStructuredOutput
    from ddtrace.llmobs._evaluators.semantic import SemanticSimilarityEvaluator
    from ddtrace.llmobs._evaluators.string_matching import RegexMatchEvaluator
    from ddtrace.llmobs._evaluators.string_matching import StringCheckEvaluator
    from ddtrace.llmobs._experiment import BaseAsyncEvaluator
    from ddtrace.llmobs._experiment import BaseAsyncSummaryEvaluator
    from ddtrace.llmobs._experiment import BaseEvaluator
    from ddtrace.llmobs._experiment import BaseSummaryEvaluator
    from ddtrace.llmobs._experiment import EvaluatorContext
    from ddtrace.llmobs._experiment import EvaluatorResult
    from ddtrace.llmobs._experiment import MultiEvaluatorResult
    from ddtrace.llmobs._experiment import RemoteEvaluator
    from ddtrace.llmobs._experiment import RemoteEvaluatorError
    from ddtrace.llmobs._experiment import SummaryEvaluatorContext
    from ddtrace.llmobs.types import JSONType


_EXPORTS = {
    "JSONEvaluator": "ddtrace.llmobs._evaluators.format",
    "LengthEvaluator": "ddtrace.llmobs._evaluators.format",
    "BooleanStructuredOutput": "ddtrace.llmobs._evaluators.llm_judge",
    "CategoricalStructuredOutput": "ddtrace.llmobs._evaluators.llm_judge",
    "LLMJudge": "ddtrace.llmobs._evaluators.llm_judge",
    "ScoreStructuredOutput": "ddtrace.llmobs._evaluators.llm_judge",
    "SemanticSimilarityEvaluator": "ddtrace.llmobs._evaluators.semantic",
    "RegexMatchEvaluator": "ddtrace.llmobs._evaluators.string_matching",
    "StringCheckEvaluator": "ddtrace.llmobs._evaluators.string_matching",
    "BaseAsyncEvaluator": "ddtrace.llmobs._experiment",
    "BaseAsyncSummaryEvaluator": "ddtrace.llmobs._experiment",
    "BaseEvaluator": "ddtrace.llmobs._experiment",
    "BaseSummaryEvaluator": "ddtrace.llmobs._experiment",
    "EvaluatorContext": "ddtrace.llmobs._experiment",
    "EvaluatorResult": "ddtrace.llmobs._experiment",
    "MultiEvaluatorResult": "ddtrace.llmobs._experiment",
    "RemoteEvaluator": "ddtrace.llmobs._experiment",
    "RemoteEvaluatorError": "ddtrace.llmobs._experiment",
    "SummaryEvaluatorContext": "ddtrace.llmobs._experiment",
    "JSONType": "ddtrace.llmobs.types",
}


__all__ = [
    "BaseAsyncEvaluator",
    "BaseAsyncSummaryEvaluator",
    "BaseEvaluator",
    "BaseSummaryEvaluator",
    "BooleanStructuredOutput",
    "CategoricalStructuredOutput",
    "EvaluatorContext",
    "EvaluatorResult",
    "JSONEvaluator",
    "JSONType",
    "LengthEvaluator",
    "LLMJudge",
    "MultiEvaluatorResult",
    "RegexMatchEvaluator",
    "RemoteEvaluator",
    "RemoteEvaluatorError",
    "ScoreStructuredOutput",
    "SemanticSimilarityEvaluator",
    "StringCheckEvaluator",
    "SummaryEvaluatorContext",
]


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(_EXPORTS[name]), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
