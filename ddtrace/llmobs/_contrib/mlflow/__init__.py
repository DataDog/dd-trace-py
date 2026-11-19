from typing import Any

from ddtrace.contrib.internal.mlflow.constants import MLFLOW_LLM_SPAN_START_EVENT
from ddtrace.contrib.internal.mlflow.constants import MLFLOW_RUN_ID_TAG
from ddtrace.internal import core
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import get_llmobs_tags


def _on_llm_span_start(span: Any, run_id: str) -> None:
    llmobs_tags = get_llmobs_tags(span) or {}
    if MLFLOW_RUN_ID_TAG not in llmobs_tags:
        _annotate_llmobs_span_data(span, tags={MLFLOW_RUN_ID_TAG: run_id})


def listen() -> None:
    core.on(MLFLOW_LLM_SPAN_START_EVENT, _on_llm_span_start)


def unlisten() -> None:
    core.reset_listeners(MLFLOW_LLM_SPAN_START_EVENT, _on_llm_span_start)
