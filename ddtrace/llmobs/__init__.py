"""
LLM Observability Service.
This is normally started automatically by including ``DD_LLMOBS_ENABLED=1 ddtrace-run <application_startup_command>``.
To start the service manually, invoke the ``enable`` method::
    from ddtrace.llmobs import LLMObs
    LLMObs.enable()
"""

from ._evaluators import BaseEvaluator
from ._evaluators import BaseSummaryEvaluator
from ._evaluators import EvaluatorContext
from ._evaluators import SummaryEvaluatorContext
from ._experiment import Dataset
from ._experiment import DatasetRecord
from ._experiment import DistributedExperiment
from ._experiment import EvaluatorResult
from ._experiment import ExperimentInfo
from ._experiment import DistributedExperimentWorker
from ._experiment import PartitionInfo
from ._experiment import build_evaluation_metric
from ._experiment import run_evaluator
from ._experiment import run_summary_evaluations
from ._experiment import run_summary_evaluator
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
    "BaseSummaryEvaluator",
    "EvaluatorContext",
    "EvaluatorResult",
    "SummaryEvaluatorContext",
    # Distributed experiment support
    "DistributedExperiment",
    "ExperimentInfo",
    "DistributedExperimentWorker",
    "PartitionInfo",
    "build_evaluation_metric",
    "run_evaluator",
    "run_summary_evaluator",
    "run_summary_evaluations",
]
