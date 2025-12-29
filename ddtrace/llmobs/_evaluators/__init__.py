"""LLMObs evaluators module.

This module provides base classes for evaluating LLM tasks in the LLMObs SDK.
"""

from ddtrace.llmobs._evaluators.base import BaseEvaluator
from ddtrace.llmobs._evaluators.base import EvaluatorContext


__all__ = [
    "BaseEvaluator",
    "EvaluatorContext",
]
