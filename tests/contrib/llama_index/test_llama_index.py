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
import pytest


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


def test_query_basic(llama_index, test_spans):
    engine = _StubQueryEngine()
    engine.query("What is the meaning of life?")

    traces = test_spans.pop_traces()
    assert len(traces) >= 1
    spans = traces[0]
    query_spans = [s for s in spans if s.name == "llama_index.query"]
    assert len(query_spans) == 1
    span = query_spans[0]
    assert span.resource == "query"
    assert span.service == "llama_index"


@pytest.mark.asyncio
async def test_aquery_async(llama_index, test_spans):
    engine = _StubQueryEngine()
    await engine.aquery("What is the meaning of life?")

    traces = test_spans.pop_traces()
    assert len(traces) >= 1
    spans = traces[0]
    aquery_spans = [s for s in spans if s.name == "llama_index.aquery"]
    assert len(aquery_spans) == 1
    span = aquery_spans[0]
    assert span.resource == "aquery"
    assert span.service == "llama_index"


def test_retrieve(llama_index, test_spans):
    retriever = _StubRetriever()
    retriever.retrieve("search query")

    traces = test_spans.pop_traces()
    assert len(traces) >= 1
    spans = traces[0]
    retrieve_spans = [s for s in spans if s.name == "llama_index.retrieve"]
    assert len(retrieve_spans) == 1
    span = retrieve_spans[0]
    assert span.resource == "retrieve"
    assert span.service == "llama_index"


def test_pipeline_run(llama_index, test_spans):
    pipeline = IngestionPipeline()
    pipeline.run(documents=[])

    traces = test_spans.pop_traces()
    assert len(traces) >= 1
    spans = traces[0]
    run_spans = [s for s in spans if s.name == "llama_index.run"]
    assert len(run_spans) == 1
    span = run_spans[0]
    assert span.resource == "run"
    assert span.service == "llama_index"


def test_multiple_queries(llama_index, test_spans):
    engine = _StubQueryEngine()
    engine.query("first query")
    engine.query("second query")

    traces = test_spans.pop_traces()
    query_spans = []
    for trace in traces:
        for span in trace:
            if span.name == "llama_index.query":
                query_spans.append(span)
    assert len(query_spans) == 2


def test_error_handling(llama_index, test_spans):
    class _ErrorQueryEngine(BaseQueryEngine):
        def __init__(self):
            super().__init__(callback_manager=None)

        def _query(self, query_bundle):
            raise ValueError("query failed")

        async def _aquery(self, query_bundle):
            raise ValueError("async query failed")

        def _get_prompt_modules(self):
            return {}

    engine = _ErrorQueryEngine()
    with pytest.raises(ValueError, match="query failed"):
        engine.query("fail query")

    traces = test_spans.pop_traces()
    assert len(traces) >= 1
    spans = traces[0]
    query_spans = [s for s in spans if s.name == "llama_index.query"]
    assert len(query_spans) == 1
    span = query_spans[0]
    assert span.error == 1


def test_synthesize(llama_index, test_spans):
    synthesizer = _StubSynthesizer()
    nodes = [NodeWithScore(node=TextNode(text="context chunk"), score=0.8)]
    synthesizer.synthesize("summarize this", nodes)

    traces = test_spans.pop_traces()
    assert len(traces) >= 1
    spans = traces[0]
    synthesize_spans = [s for s in spans if s.name == "llama_index.synthesize"]
    assert len(synthesize_spans) == 1
    span = synthesize_spans[0]
    assert span.resource == "synthesize"
    assert span.service == "llama_index"
