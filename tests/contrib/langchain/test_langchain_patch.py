from ddtrace.contrib.langchain import patch
from ddtrace.contrib.langchain import unpatch
from ddtrace.contrib.langchain.constants import text_embedding_models
from ddtrace.contrib.langchain.constants import vectorstores
from tests.contrib.patch import PatchTestCase


class TestLangchainPatch(PatchTestCase.Base):
    __integration_name__ = "langchain"
    __module_name__ = "langchain"
    __patch_func__ = patch
    __unpatch_func__ = unpatch

    def assert_module_patched(self, langchain):
        self.assert_wrapped(langchain.llms.base.BaseLLM.generate)
        self.assert_wrapped(langchain.llms.base.BaseLLM.agenerate)
        self.assert_wrapped(langchain.chat_models.base.BaseChatModel.generate)
        self.assert_wrapped(langchain.chat_models.base.BaseChatModel.agenerate)
        self.assert_wrapped(langchain.chains.base.Chain.__call__)
        self.assert_wrapped(langchain.chains.base.Chain.acall)
        for text_embedding_model in text_embedding_models:
            embedding_model = getattr(langchain.embeddings, text_embedding_model, None)
            if embedding_model:
                self.assert_wrapped(embedding_model.embed_query)
                self.assert_wrapped(embedding_model.embed_documents)
        for vectorstore in vectorstores:
            vectorstore_interface = getattr(langchain.vectorstores, vectorstore, None)
            if vectorstore_interface:
                self.assert_wrapped(vectorstore_interface.similarity_search)

    def assert_not_module_patched(self, langchain):
        self.assert_not_wrapped(langchain.llms.base.BaseLLM.generate)
        self.assert_not_wrapped(langchain.llms.base.BaseLLM.agenerate)
        self.assert_not_wrapped(langchain.chat_models.base.BaseChatModel.generate)
        self.assert_not_wrapped(langchain.chat_models.base.BaseChatModel.agenerate)
        self.assert_not_wrapped(langchain.chains.base.Chain.__call__)
        self.assert_not_wrapped(langchain.chains.base.Chain.acall)
        for text_embedding_model in text_embedding_models:
            embedding_model = getattr(langchain.embeddings, text_embedding_model, None)
            if embedding_model:
                self.assert_not_wrapped(embedding_model.embed_query)
                self.assert_not_wrapped(embedding_model.embed_documents)
        for vectorstore in vectorstores:
            vectorstore_interface = getattr(langchain.vectorstores, vectorstore, None)
            if vectorstore_interface:
                self.assert_not_wrapped(vectorstore_interface.similarity_search)

    def assert_not_module_double_patched(self, langchain):
        self.assert_not_double_wrapped(langchain.llms.base.BaseLLM.generate)
        self.assert_not_double_wrapped(langchain.llms.base.BaseLLM.agenerate)
        self.assert_not_double_wrapped(langchain.chat_models.base.BaseChatModel.generate)
        self.assert_not_double_wrapped(langchain.chat_models.base.BaseChatModel.agenerate)
        self.assert_not_double_wrapped(langchain.chains.base.Chain.__call__)
        self.assert_not_double_wrapped(langchain.chains.base.Chain.acall)
        for text_embedding_model in text_embedding_models:
            embedding_model = getattr(langchain.embeddings, text_embedding_model, None)
            if embedding_model:
                self.assert_not_double_wrapped(embedding_model.embed_query)
                self.assert_not_double_wrapped(embedding_model.embed_documents)
        for vectorstore in vectorstores:
            vectorstore_interface = getattr(langchain.vectorstores, vectorstore, None)
            if vectorstore_interface:
                self.assert_not_double_wrapped(vectorstore_interface.similarity_search)
