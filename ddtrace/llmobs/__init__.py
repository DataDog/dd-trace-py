"""
LLM Observability Service.
This is normally started automatically by including ``DD_LLMOBS_ENABLED=1 ddtrace-run <application_startup_command>``.
To start the service manually, invoke the ``enable`` method::
    from ddtrace.llmobs import LLMObs
    LLMObs.enable()
"""

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
from ddtrace.llmobs._llmobs import LLMObs
from ddtrace.llmobs._llmobs import LLMObsSpan
from ddtrace.llmobs._memory import DatadogMemoryBackend
from ddtrace.llmobs._memory import EmptyMemoryBackend
from ddtrace.llmobs._memory import Memory
from ddtrace.llmobs._memory import MemoryBackend
from ddtrace.llmobs._memory import MemoryBackendError
from ddtrace.llmobs._memory import MemoryLink
from ddtrace.llmobs._memory import MemoryNotConfiguredError
from ddtrace.llmobs._memory import MemoryPersistUnsupportedError
from ddtrace.llmobs._memory import MemoryRecord
from ddtrace.llmobs._memory import MemoryScope
from ddtrace.llmobs._memory import MemorySource
from ddtrace.llmobs._memory import get_memory_scope
from ddtrace.llmobs._memory import memory_scope
from ddtrace.llmobs._memory import set_memory_scope
from ddtrace.llmobs.types import Prompt


__all__ = [
    "LLMObs",
    "LLMObsSpan",
    "Dataset",
    "DatasetRecord",
    "DatadogMemoryBackend",
    "EmptyMemoryBackend",
    "Memory",
    "MemoryBackend",
    "MemoryBackendError",
    "MemoryLink",
    "MemoryNotConfiguredError",
    "MemoryPersistUnsupportedError",
    "MemoryRecord",
    "MemoryScope",
    "MemorySource",
    "Prompt",
    "BaseEvaluator",
    "BaseSummaryEvaluator",
    "BaseAsyncEvaluator",
    "BaseAsyncSummaryEvaluator",
    "BooleanStructuredOutput",
    "CategoricalStructuredOutput",
    "EvaluatorContext",
    "EvaluatorResult",
    "LLMJudge",
    "RemoteEvaluator",
    "RemoteEvaluatorError",
    "ScoreStructuredOutput",
    "SummaryEvaluatorContext",
    "get_memory_scope",
    "memory_scope",
    "set_memory_scope",
]
