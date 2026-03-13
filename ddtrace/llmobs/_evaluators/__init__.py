"""LLMObs evaluators module.

This module provides base classes and built-in evaluators for evaluating LLM tasks in the LLMObs SDK.
"""

from ddtrace.llmobs._evaluators.format import JSONEvaluator
from ddtrace.llmobs._evaluators.format import LengthEvaluator
from ddtrace.llmobs._evaluators.llm_judge import BooleanStructuredOutput
from ddtrace.llmobs._evaluators.llm_judge import CategoricalStructuredOutput
from ddtrace.llmobs._evaluators.llm_judge import LLMJudge
from ddtrace.llmobs._evaluators.llm_judge import ScoreStructuredOutput
from ddtrace.llmobs._evaluators.metrics import AlignmentConfig
from ddtrace.llmobs._evaluators.metrics import BiasEvaluator
from ddtrace.llmobs._evaluators.metrics import ClassConfig
from ddtrace.llmobs._evaluators.metrics import CorrelationEvaluator
from ddtrace.llmobs._evaluators.metrics import DisagreementEvaluator
from ddtrace.llmobs._evaluators.metrics import MAEEvaluator
from ddtrace.llmobs._evaluators.metrics import RMSEEvaluator
from ddtrace.llmobs._evaluators.metrics import TNREvaluator
from ddtrace.llmobs._evaluators.metrics import TPREvaluator
from ddtrace.llmobs._evaluators.metrics import compute_consistency
from ddtrace.llmobs._evaluators.semantic import SemanticSimilarityEvaluator
from ddtrace.llmobs._evaluators.string_matching import RegexMatchEvaluator
from ddtrace.llmobs._evaluators.string_matching import StringCheckEvaluator
from ddtrace.llmobs._experiment import BaseAsyncEvaluator
from ddtrace.llmobs._experiment import BaseAsyncSummaryEvaluator
from ddtrace.llmobs._experiment import BaseEvaluator
from ddtrace.llmobs._experiment import BaseSummaryEvaluator
from ddtrace.llmobs._experiment import EvaluatorContext
from ddtrace.llmobs._experiment import EvaluatorResult
from ddtrace.llmobs._experiment import RemoteEvaluator
from ddtrace.llmobs._experiment import RemoteEvaluatorError
from ddtrace.llmobs._experiment import SummaryEvaluatorContext
from ddtrace.llmobs.types import JSONType


__all__ = [
    "AlignmentConfig",
    "BaseAsyncEvaluator",
    "BaseAsyncSummaryEvaluator",
    "BaseEvaluator",
    "BaseSummaryEvaluator",
    "BiasEvaluator",
    "BooleanStructuredOutput",
    "CategoricalStructuredOutput",
    "ClassConfig",
    "compute_consistency",
    "CorrelationEvaluator",
    "DisagreementEvaluator",
    "EvaluatorContext",
    "EvaluatorResult",
    "JSONEvaluator",
    "JSONType",
    "LengthEvaluator",
    "LLMJudge",
    "MAEEvaluator",
    "RegexMatchEvaluator",
    "RMSEEvaluator",
    "RemoteEvaluator",
    "RemoteEvaluatorError",
    "ScoreStructuredOutput",
    "SemanticSimilarityEvaluator",
    "StringCheckEvaluator",
    "SummaryEvaluatorContext",
    "TNREvaluator",
    "TPREvaluator",
]
