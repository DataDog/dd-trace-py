"""
LLM Observability Service.
This is normally started automatically by including ``DD_LLMOBS_ENABLED=1 ddtrace-run <application_startup_command>``.
To start the service manually, invoke the ``enable`` method::
    from ddtrace.llmobs import LLMObs
    LLMObs.enable()
"""

from importlib import import_module
from typing import TYPE_CHECKING
from typing import Any


if TYPE_CHECKING:
    from ddtrace.llmobs._evaluators import BaseAsyncEvaluator
    from ddtrace.llmobs._evaluators import BaseAsyncSummaryEvaluator
    from ddtrace.llmobs._evaluators import BaseEvaluator
    from ddtrace.llmobs._evaluators import BaseSummaryEvaluator
    from ddtrace.llmobs._evaluators import BooleanStructuredOutput
    from ddtrace.llmobs._evaluators import CategoricalStructuredOutput
    from ddtrace.llmobs._evaluators import EvaluatorContext
    from ddtrace.llmobs._evaluators import LLMJudge
    from ddtrace.llmobs._evaluators import RemoteEvaluator
    from ddtrace.llmobs._evaluators import RemoteEvaluatorError
    from ddtrace.llmobs._evaluators import ScoreStructuredOutput
    from ddtrace.llmobs._evaluators import SummaryEvaluatorContext
    from ddtrace.llmobs._experiment import Dataset
    from ddtrace.llmobs._experiment import DatasetRecord
    from ddtrace.llmobs._experiment import EvaluatorResult
    from ddtrace.llmobs._experiment import ExperimentSummary
    from ddtrace.llmobs._experiment import MultiEvaluatorResult
    from ddtrace.llmobs._llmobs import LLMObs
    from ddtrace.llmobs._llmobs import LLMObsSpan
    from ddtrace.llmobs.types import Agent
    from ddtrace.llmobs.types import Prompt


_EXPORTS = {
    "BaseAsyncEvaluator": "ddtrace.llmobs._evaluators",
    "BaseAsyncSummaryEvaluator": "ddtrace.llmobs._evaluators",
    "BaseEvaluator": "ddtrace.llmobs._evaluators",
    "BaseSummaryEvaluator": "ddtrace.llmobs._evaluators",
    "BooleanStructuredOutput": "ddtrace.llmobs._evaluators",
    "CategoricalStructuredOutput": "ddtrace.llmobs._evaluators",
    "EvaluatorContext": "ddtrace.llmobs._evaluators",
    "LLMJudge": "ddtrace.llmobs._evaluators",
    "RemoteEvaluator": "ddtrace.llmobs._evaluators",
    "RemoteEvaluatorError": "ddtrace.llmobs._evaluators",
    "ScoreStructuredOutput": "ddtrace.llmobs._evaluators",
    "SummaryEvaluatorContext": "ddtrace.llmobs._evaluators",
    "Dataset": "ddtrace.llmobs._experiment",
    "DatasetRecord": "ddtrace.llmobs._experiment",
    "EvaluatorResult": "ddtrace.llmobs._experiment",
    "ExperimentSummary": "ddtrace.llmobs._experiment",
    "MultiEvaluatorResult": "ddtrace.llmobs._experiment",
    "LLMObs": "ddtrace.llmobs._llmobs",
    "LLMObsSpan": "ddtrace.llmobs._llmobs",
    "Agent": "ddtrace.llmobs.types",
    "Prompt": "ddtrace.llmobs.types",
}


__all__ = [
    "LLMObs",
    "LLMObsSpan",
    "Agent",
    "Dataset",
    "DatasetRecord",
    "Prompt",
    "BaseEvaluator",
    "BaseSummaryEvaluator",
    "BaseAsyncEvaluator",
    "BaseAsyncSummaryEvaluator",
    "BooleanStructuredOutput",
    "CategoricalStructuredOutput",
    "EvaluatorContext",
    "EvaluatorResult",
    "ExperimentSummary",
    "MultiEvaluatorResult",
    "LLMJudge",
    "RemoteEvaluator",
    "RemoteEvaluatorError",
    "ScoreStructuredOutput",
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
