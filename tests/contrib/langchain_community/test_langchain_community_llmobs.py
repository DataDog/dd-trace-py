import os

import mock
import pytest

from ddtrace import patch
from ddtrace.llmobs import LLMObs
from tests.llmobs._utils import _expected_llmobs_llm_span_event
from tests.subprocesstest import SubprocessTestCase
from tests.subprocesstest import run_in_subprocess


def test_llmobs_embedding_documents(langchain_community, llmobs_events, tracer):
    embedding_model = langchain_community.embeddings.FakeEmbeddings(size=1536)
    embedding_model.embed_documents(["hello world", "goodbye world"])

    trace = tracer.pop_traces()[0]
    span = trace[0] if isinstance(trace, list) else trace
    assert len(llmobs_events) == 1
    assert llmobs_events[0] == _expected_llmobs_llm_span_event(
        span,
        span_kind="embedding",
        model_name="",
        model_provider="fake",
        input_documents=[{"text": "hello world"}, {"text": "goodbye world"}],
        output_value="[2 embedding(s) returned with size 1536]",
        tags={"ml_app": "langchain_test", "service": "tests.contrib.langchain_community"},
    )


def test_llmobs_embedding_query(langchain_openai, llmobs_events, tracer, openai_url):
    if langchain_openai is None:
        pytest.skip("langchain_openai not installed which is required for this test.")
    embedding_model = langchain_openai.embeddings.OpenAIEmbeddings(base_url=openai_url)
    embedding_model.embed_query("hello world")
    trace = tracer.pop_traces()[0]
    assert len(llmobs_events) == 1
    assert llmobs_events[0] == _expected_llmobs_llm_span_event(
        trace[0],
        span_kind="embedding",
        model_name=embedding_model.model,
        model_provider="openai",
        input_documents=[{"text": "hello world"}],
        output_value="[1 embedding(s) returned with size 1536]",
        tags={"ml_app": "langchain_test", "service": "tests.contrib.langchain_community"},
    )


class TestTraceStructureWithLLMIntegrations(SubprocessTestCase):
    openai_env_config = dict(
        OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", "testing"),
        DD_API_KEY="<not-a-real-key>",
    )

    def setUp(self):
        patcher = mock.patch("ddtrace.llmobs._llmobs.LLMObsSpanWriter")
        LLMObsSpanWriterMock = patcher.start()
        mock_llmobs_span_writer = mock.MagicMock()
        LLMObsSpanWriterMock.return_value = mock_llmobs_span_writer

        self.mock_llmobs_span_writer = mock_llmobs_span_writer

        super(TestTraceStructureWithLLMIntegrations, self).setUp()

    def tearDown(self):
        LLMObs.disable()

    def _assert_trace_structure_from_writer_call_args(self, span_kinds):
        assert self.mock_llmobs_span_writer.enqueue.call_count == len(span_kinds)

        calls = self.mock_llmobs_span_writer.enqueue.call_args_list[::-1]

        for span_kind, call in zip(span_kinds, calls):
            call_args = call.args[0]

            assert (
                call_args["meta"]["span.kind"] == span_kind
            ), f"Span kind is {call_args['meta']['span.kind']} but expected {span_kind}"
            if span_kind == "workflow":
                assert len(call_args["meta"]["input"]["value"]) > 0
                assert len(call_args["meta"]["output"]["value"]) > 0
            elif span_kind == "llm":
                assert len(call_args["meta"]["input"]["messages"]) > 0
                assert len(call_args["meta"]["output"]["messages"]) > 0
            elif span_kind == "embedding":
                assert len(call_args["meta"]["input"]["documents"]) > 0
                assert len(call_args["meta"]["output"]["value"]) > 0

    @staticmethod
    def _call_openai_embedding(OpenAIEmbeddings):
        embedding = OpenAIEmbeddings(base_url="http://localhost:9126/vcr/openai")
        with mock.patch("langchain_openai.embeddings.base.tiktoken.encoding_for_model") as mock_encoding_for_model:
            mock_encoding = mock.MagicMock()
            mock_encoding_for_model.return_value = mock_encoding
            mock_encoding.encode.return_value = [0.0] * 1536
            embedding.embed_query("hello world")

    @run_in_subprocess(env_overrides=openai_env_config)
    def test_llmobs_langchain_with_embedding_model_openai_enabled(self):
        import langchain_community  # noqa: F401
        from langchain_openai import OpenAIEmbeddings

        patch(openai=True, langchain_community=True)

        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False)
        self._call_openai_embedding(OpenAIEmbeddings)
        self._assert_trace_structure_from_writer_call_args(["workflow", "embedding"])

    @run_in_subprocess(env_overrides=openai_env_config)
    def test_llmobs_langchain_with_embedding_model_openai_disabled(self):
        import langchain_community  # noqa: F401
        from langchain_openai import OpenAIEmbeddings

        patch(langchain_community=True)
        LLMObs.enable(ml_app="<ml-app-name>", integrations_enabled=False)
        self._call_openai_embedding(OpenAIEmbeddings)
        self._assert_trace_structure_from_writer_call_args(["embedding"])
