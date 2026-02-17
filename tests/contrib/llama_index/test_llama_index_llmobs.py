from typing import Any
from typing import Sequence

from llama_index.core.base.base_query_engine import BaseQueryEngine
from llama_index.core.base.base_retriever import BaseRetriever
from llama_index.core.base.response.schema import Response
from llama_index.core.ingestion.pipeline import IngestionPipeline
from llama_index.core.prompts.mixin import PromptDictType
from llama_index.core.response_synthesizers.base import BaseSynthesizer
from llama_index.core.schema import NodeWithScore
from llama_index.core.schema import TextNode
from llama_index.core.types import RESPONSE_TEXT_TYPE
import mock

from tests.llmobs._utils import _expected_llmobs_non_llm_span_event


class _StubQueryEngine(BaseQueryEngine):
    def __init__(self):
        super().__init__(callback_manager=None)

    def _query(self, query_bundle):
        return Response(response="query response")

    async def _aquery(self, query_bundle):
        return Response(response="async query response")

    def _get_prompt_modules(self):
        return {}


class _StubRetriever(BaseRetriever):
    def __init__(self):
        super().__init__(callback_manager=None)

    def _retrieve(self, query_bundle):
        return [NodeWithScore(node=TextNode(text="retrieved node"), score=0.9)]

    def _get_prompt_modules(self):
        return {}


class _StubSynthesizer(BaseSynthesizer):
    def _get_prompts(self) -> PromptDictType:
        return {}

    def _update_prompts(self, prompts: PromptDictType) -> None:
        pass

    def get_response(self, query_str: str, text_chunks: Sequence[str], **response_kwargs: Any) -> RESPONSE_TEXT_TYPE:
        return "synthesized response"

    async def aget_response(
        self, query_str: str, text_chunks: Sequence[str], **response_kwargs: Any
    ) -> RESPONSE_TEXT_TYPE:
        return "async synthesized response"


EXPECTED_SPAN_ARGS = {
    "input_value": mock.ANY,
    "output_value": mock.ANY,
    "metadata": mock.ANY,
    "tags": {"service": "llama_index", "ml_app": "<ml-app-name>"},
}


def test_llmobs_query(llama_index, test_spans, llmobs_events):
    engine = _StubQueryEngine()
    engine.query("What is the meaning of life?")

    traces = test_spans.pop_traces()
    spans = traces[0]
    query_spans = [s for s in spans if s.name == "llama_index.query"]
    assert len(query_spans) == 1
    span = query_spans[0]

    llmobs_query_events = [e for e in llmobs_events if e.get("name") == "llama_index.query"]
    assert len(llmobs_query_events) == 1
    assert llmobs_query_events[0] == _expected_llmobs_non_llm_span_event(
        span,
        span_kind="workflow",
        **EXPECTED_SPAN_ARGS,
    )


def test_llmobs_retrieve(llama_index, test_spans, llmobs_events):
    retriever = _StubRetriever()
    retriever.retrieve("search query")

    traces = test_spans.pop_traces()
    spans = traces[0]
    retrieve_spans = [s for s in spans if s.name == "llama_index.retrieve"]
    assert len(retrieve_spans) == 1
    span = retrieve_spans[0]

    llmobs_retrieve_events = [e for e in llmobs_events if e.get("name") == "llama_index.retrieve"]
    assert len(llmobs_retrieve_events) == 1
    assert llmobs_retrieve_events[0] == _expected_llmobs_non_llm_span_event(
        span,
        span_kind="workflow",
        **EXPECTED_SPAN_ARGS,
    )


def test_llmobs_pipeline_run(llama_index, test_spans, llmobs_events):
    pipeline = IngestionPipeline()
    pipeline.run(documents=[])

    traces = test_spans.pop_traces()
    spans = traces[0]
    run_spans = [s for s in spans if s.name == "llama_index.run"]
    assert len(run_spans) == 1
    span = run_spans[0]

    llmobs_run_events = [e for e in llmobs_events if e.get("name") == "llama_index.run"]
    assert len(llmobs_run_events) == 1
    assert llmobs_run_events[0] == _expected_llmobs_non_llm_span_event(
        span,
        span_kind="workflow",
        **EXPECTED_SPAN_ARGS,
    )


def test_llmobs_synthesize(llama_index, test_spans, llmobs_events):
    synthesizer = _StubSynthesizer()
    nodes = [NodeWithScore(node=TextNode(text="context chunk"), score=0.8)]
    synthesizer.synthesize("summarize this", nodes)

    traces = test_spans.pop_traces()
    spans = traces[0]
    synthesize_spans = [s for s in spans if s.name == "llama_index.synthesize"]
    assert len(synthesize_spans) == 1
    span = synthesize_spans[0]

    llmobs_synthesize_events = [e for e in llmobs_events if e.get("name") == "llama_index.synthesize"]
    assert len(llmobs_synthesize_events) == 1
    assert llmobs_synthesize_events[0] == _expected_llmobs_non_llm_span_event(
        span,
        span_kind="workflow",
        **EXPECTED_SPAN_ARGS,
    )
