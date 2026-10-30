import pytest

from ddtrace.contrib.internal.llama_index.patch import _DD_WRAPPED
from ddtrace.contrib.internal.llama_index.patch import get_version
from ddtrace.contrib.internal.llama_index.patch import patch
from ddtrace.contrib.internal.llama_index.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestLlamaIndexPatch(PatchTestCase.Base):
    __integration_name__ = "llama_index"
    __module_name__ = "llama_index.core"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    # llama_index uses wrapt FunctionWrappers for its own instrumentation
    # (@dispatcher.span on query, retrieve, etc.). The default is_wrapped() checks
    # is_wrapted() which returns True for these pre-existing wrapt wrappers even after
    # dd-trace unpatch. Override to only check for our __dd_wrapped__ marker.
    def is_wrapped(self, obj):
        return hasattr(obj, _DD_WRAPPED)

    def assert_not_double_wrapped(self, obj):
        self.assert_wrapped(obj)
        inner = getattr(obj, _DD_WRAPPED)
        self.assert_not_wrapped(inner)

    # BaseWorkflowAgent only exists in newer llama-index-core versions.
    # Import it optionally so tests pass on both ~=0.11.0 and latest.
    @staticmethod
    def _try_import_agent():
        try:
            from llama_index.core.agent.workflow.base_agent import BaseWorkflowAgent

            return BaseWorkflowAgent
        except (ImportError, ModuleNotFoundError):
            return None

    def assert_module_patched(self, llama_index):
        from llama_index.core.base.base_query_engine import BaseQueryEngine
        from llama_index.core.base.base_retriever import BaseRetriever
        from llama_index.core.base.embeddings.base import BaseEmbedding
        from llama_index.core.base.llms.base import BaseLLM

        self.assert_wrapped(BaseLLM.chat)
        self.assert_wrapped(BaseLLM.complete)
        self.assert_wrapped(BaseLLM.stream_chat)
        self.assert_wrapped(BaseLLM.stream_complete)
        self.assert_wrapped(BaseLLM.achat)
        self.assert_wrapped(BaseLLM.acomplete)
        self.assert_wrapped(BaseLLM.astream_chat)
        self.assert_wrapped(BaseLLM.astream_complete)
        self.assert_wrapped(BaseQueryEngine.query)
        self.assert_wrapped(BaseQueryEngine.aquery)
        self.assert_wrapped(BaseRetriever.retrieve)
        self.assert_wrapped(BaseRetriever.aretrieve)
        self.assert_wrapped(BaseEmbedding.get_query_embedding)
        self.assert_wrapped(BaseEmbedding.get_text_embedding_batch)
        BaseWorkflowAgent = self._try_import_agent()
        if BaseWorkflowAgent is not None:
            self.assert_wrapped(BaseWorkflowAgent.run)
            self.assert_wrapped(BaseWorkflowAgent.call_tool)

    def assert_not_module_patched(self, llama_index):
        from llama_index.core.base.base_query_engine import BaseQueryEngine
        from llama_index.core.base.base_retriever import BaseRetriever
        from llama_index.core.base.embeddings.base import BaseEmbedding
        from llama_index.core.base.llms.base import BaseLLM

        self.assert_not_wrapped(BaseLLM.chat)
        self.assert_not_wrapped(BaseLLM.complete)
        self.assert_not_wrapped(BaseLLM.stream_chat)
        self.assert_not_wrapped(BaseLLM.stream_complete)
        self.assert_not_wrapped(BaseLLM.achat)
        self.assert_not_wrapped(BaseLLM.acomplete)
        self.assert_not_wrapped(BaseLLM.astream_chat)
        self.assert_not_wrapped(BaseLLM.astream_complete)
        self.assert_not_wrapped(BaseQueryEngine.query)
        self.assert_not_wrapped(BaseQueryEngine.aquery)
        self.assert_not_wrapped(BaseRetriever.retrieve)
        self.assert_not_wrapped(BaseRetriever.aretrieve)
        self.assert_not_wrapped(BaseEmbedding.get_query_embedding)
        self.assert_not_wrapped(BaseEmbedding.get_text_embedding_batch)
        BaseWorkflowAgent = self._try_import_agent()
        if BaseWorkflowAgent is not None:
            self.assert_not_wrapped(BaseWorkflowAgent.run)
            self.assert_not_wrapped(BaseWorkflowAgent.call_tool)

    def assert_not_module_double_patched(self, llama_index):
        from llama_index.core.base.base_query_engine import BaseQueryEngine
        from llama_index.core.base.base_retriever import BaseRetriever
        from llama_index.core.base.embeddings.base import BaseEmbedding
        from llama_index.core.base.llms.base import BaseLLM

        self.assert_not_double_wrapped(BaseLLM.chat)
        self.assert_not_double_wrapped(BaseLLM.complete)
        self.assert_not_double_wrapped(BaseLLM.stream_chat)
        self.assert_not_double_wrapped(BaseLLM.stream_complete)
        self.assert_not_double_wrapped(BaseLLM.achat)
        self.assert_not_double_wrapped(BaseLLM.acomplete)
        self.assert_not_double_wrapped(BaseLLM.astream_chat)
        self.assert_not_double_wrapped(BaseLLM.astream_complete)
        self.assert_not_double_wrapped(BaseQueryEngine.query)
        self.assert_not_double_wrapped(BaseQueryEngine.aquery)
        self.assert_not_double_wrapped(BaseRetriever.retrieve)
        self.assert_not_double_wrapped(BaseRetriever.aretrieve)
        self.assert_not_double_wrapped(BaseEmbedding.get_query_embedding)
        self.assert_not_double_wrapped(BaseEmbedding.get_text_embedding_batch)
        BaseWorkflowAgent = self._try_import_agent()
        if BaseWorkflowAgent is not None:
            self.assert_not_double_wrapped(BaseWorkflowAgent.run)
            self.assert_not_double_wrapped(BaseWorkflowAgent.call_tool)


@pytest.mark.subprocess(env=dict(DD_LLMOBS_ENABLED="false"), err=None)
def test_manual_patch_emits_shadow_tags():
    from unittest.mock import patch as mock_patch

    from llama_index.core.llms import ChatMessage
    from llama_index.core.llms import ChatResponse
    from llama_index.llms.openai import OpenAI

    import ddtrace
    from tests.utils import DummyWriter

    writer = DummyWriter()
    ddtrace.tracer._span_aggregator.writer = writer
    ddtrace.patch(llama_index=True)

    response = ChatResponse(
        message=ChatMessage(role="assistant", content="Hello"),
        raw={"usage": {"prompt_tokens": 14, "completion_tokens": 15, "total_tokens": 29}},
    )
    llm = OpenAI(model="gpt-4o-mini", api_key="not-a-real-key")
    with mock_patch.object(OpenAI, "_chat", return_value=response):
        llm.chat([ChatMessage(role="user", content="Hello")])

    spans = [span for span in writer.pop() if span.name == "llama_index.request"]
    assert len(spans) == 1
    span = spans[0]
    assert span.get_tag("_dd.llmobs.span_kind") == "llm"
    assert span.get_tag("_dd.llmobs.model_name") == "gpt-4o-mini"
    assert span.get_tag("_dd.llmobs.model_provider") == "openai"
    assert span.get_metric("_dd.llmobs.enabled") == 0
    assert span.get_metric("_dd.llmobs.input_tokens") == 14
    assert span.get_metric("_dd.llmobs.output_tokens") == 15
    assert span.get_metric("_dd.llmobs.total_tokens") == 29
