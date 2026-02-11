from ddtrace.contrib.internal.llama_index.patch import get_version
from ddtrace.contrib.internal.llama_index.patch import patch

try:
    from ddtrace.contrib.internal.llama_index.patch import unpatch
except ImportError:
    unpatch = None

from tests.contrib.patch import PatchTestCase


class TestLlamaIndexPatch(PatchTestCase.Base):
    __integration_name__ = "llama_index"
    __module_name__ = "llama_index"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, llama_index):
        self.assert_wrapped(llama_index.query)
        self.assert_wrapped(llama_index.aquery)
        self.assert_wrapped(llama_index.retrieve)
        self.assert_wrapped(llama_index.aretrieve)
        self.assert_wrapped(llama_index.chat)
        self.assert_wrapped(llama_index.complete)
        self.assert_wrapped(llama_index.stream_chat)
        self.assert_wrapped(llama_index.stream_complete)
        self.assert_wrapped(llama_index.achat)
        self.assert_wrapped(llama_index.acomplete)
        self.assert_wrapped(llama_index.astream_chat)
        self.assert_wrapped(llama_index.astream_complete)
        self.assert_wrapped(llama_index.get_query_embedding)
        self.assert_wrapped(llama_index.aget_query_embedding)
        self.assert_wrapped(llama_index.get_text_embedding_batch)
        self.assert_wrapped(llama_index.aget_text_embedding_batch)
        self.assert_wrapped(llama_index.take_step)
        self.assert_wrapped(llama_index.run_agent_step)

    def assert_not_module_patched(self, llama_index):
        self.assert_not_wrapped(llama_index.query)
        self.assert_not_wrapped(llama_index.aquery)
        self.assert_not_wrapped(llama_index.retrieve)
        self.assert_not_wrapped(llama_index.aretrieve)
        self.assert_not_wrapped(llama_index.chat)
        self.assert_not_wrapped(llama_index.complete)
        self.assert_not_wrapped(llama_index.stream_chat)
        self.assert_not_wrapped(llama_index.stream_complete)
        self.assert_not_wrapped(llama_index.achat)
        self.assert_not_wrapped(llama_index.acomplete)
        self.assert_not_wrapped(llama_index.astream_chat)
        self.assert_not_wrapped(llama_index.astream_complete)
        self.assert_not_wrapped(llama_index.get_query_embedding)
        self.assert_not_wrapped(llama_index.aget_query_embedding)
        self.assert_not_wrapped(llama_index.get_text_embedding_batch)
        self.assert_not_wrapped(llama_index.aget_text_embedding_batch)
        self.assert_not_wrapped(llama_index.take_step)
        self.assert_not_wrapped(llama_index.run_agent_step)

    def assert_not_module_double_patched(self, llama_index):
        self.assert_not_double_wrapped(llama_index.query)
        self.assert_not_double_wrapped(llama_index.aquery)
        self.assert_not_double_wrapped(llama_index.retrieve)
        self.assert_not_double_wrapped(llama_index.aretrieve)
        self.assert_not_double_wrapped(llama_index.chat)
        self.assert_not_double_wrapped(llama_index.complete)
        self.assert_not_double_wrapped(llama_index.stream_chat)
        self.assert_not_double_wrapped(llama_index.stream_complete)
        self.assert_not_double_wrapped(llama_index.achat)
        self.assert_not_double_wrapped(llama_index.acomplete)
        self.assert_not_double_wrapped(llama_index.astream_chat)
        self.assert_not_double_wrapped(llama_index.astream_complete)
        self.assert_not_double_wrapped(llama_index.get_query_embedding)
        self.assert_not_double_wrapped(llama_index.aget_query_embedding)
        self.assert_not_double_wrapped(llama_index.get_text_embedding_batch)
        self.assert_not_double_wrapped(llama_index.aget_text_embedding_batch)
        self.assert_not_double_wrapped(llama_index.take_step)
        self.assert_not_double_wrapped(llama_index.run_agent_step)
