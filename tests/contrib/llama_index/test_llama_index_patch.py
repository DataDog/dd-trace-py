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

    # AIDEV-NOTE: llama_index uses wrapt FunctionWrappers for its own instrumentation
    # (@dispatcher.span on query, retrieve, etc.). The default is_wrapped() checks
    # is_wrapted() which returns True for these pre-existing wrapt wrappers even after
    # dd-trace unpatch. Override to only check for our __dd_wrapped__ marker.
    def is_wrapped(self, obj):
        return hasattr(obj, _DD_WRAPPED)

    def assert_not_double_wrapped(self, obj):
        self.assert_wrapped(obj)
        inner = getattr(obj, _DD_WRAPPED)
        self.assert_not_wrapped(inner)

    # AIDEV-NOTE: BaseWorkflowAgent only exists in newer llama-index-core versions.
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
