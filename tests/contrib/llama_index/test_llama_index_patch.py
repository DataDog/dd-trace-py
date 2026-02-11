from ddtrace.contrib.internal.llama_index.patch import get_version
from ddtrace.contrib.internal.llama_index.patch import patch

try:
    from ddtrace.contrib.internal.llama_index.patch import unpatch
except ImportError:
    unpatch = None

from tests.contrib.patch import PatchTestCase


class TestLlamaIndexPatch(PatchTestCase.Base):
    __integration_name__ = "llama_index"
    __module_name__ = "llama_index.core"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, llama_index):
        from llama_index.core.base.base_query_engine import BaseQueryEngine
        from llama_index.core.base.base_retriever import BaseRetriever
        from llama_index.core.base.embeddings.base import BaseEmbedding
        from llama_index.core.base.llms.base import BaseLLM

        # Concrete base class methods
        self.assert_wrapped(BaseQueryEngine.query)
        self.assert_wrapped(BaseQueryEngine.aquery)
        self.assert_wrapped(BaseRetriever.retrieve)
        self.assert_wrapped(BaseRetriever.aretrieve)
        self.assert_wrapped(BaseEmbedding.get_query_embedding)
        self.assert_wrapped(BaseEmbedding.aget_query_embedding)
        self.assert_wrapped(BaseEmbedding.get_text_embedding_batch)
        self.assert_wrapped(BaseEmbedding.aget_text_embedding_batch)

        # BaseLLM.__init__ hook for wrapping abstract methods on subclasses
        self.assert_wrapped(BaseLLM.__init__)

    def assert_not_module_patched(self, llama_index):
        from llama_index.core.base.llms.base import BaseLLM

        # llama_index's @dispatcher.span decorator means BaseQueryEngine,
        # BaseRetriever, and BaseEmbedding methods are always wrapt
        # FunctionWrapper instances. We verify our __init__ hook is removed
        # and the module marker is cleared.
        self.assert_not_wrapped(BaseLLM.__init__)
        self.assertFalse(getattr(llama_index, "_datadog_patch", False))

    def assert_not_module_double_patched(self, llama_index):
        from llama_index.core.base.llms.base import BaseLLM

        # llama_index's @dispatcher.span already wraps methods with
        # wrapt FunctionWrapper, making standard double-wrap checks
        # unreliable. We verify the __init__ hook is not double-wrapped
        # and module marker is set exactly once.
        self.assert_not_double_wrapped(BaseLLM.__init__)
        self.assertTrue(getattr(llama_index, "_datadog_patch", False))
