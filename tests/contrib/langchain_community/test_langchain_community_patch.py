from ddtrace.contrib.internal.langchain_community.constants import text_embedding_models
from ddtrace.contrib.internal.langchain_community.constants import vectorstore_classes
from ddtrace.contrib.internal.langchain_community.patch import get_version
from ddtrace.contrib.internal.langchain_community.patch import patch
from ddtrace.contrib.internal.langchain_community.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestLangchainPatch(PatchTestCase.Base):
    __integration_name__ = "langchain_community"
    __module_name__ = "langchain_community"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, langchain):
        try:
            import langchain_community
        except ImportError:
            langchain_community = None

        import langchain_openai
        import langchain_pinecone

        self.assert_wrapped(langchain_openai.OpenAIEmbeddings.embed_documents)
        self.assert_wrapped(langchain_pinecone.PineconeVectorStore.similarity_search)

        if not langchain_community:
            return
        for text_embedding_model in text_embedding_models:
            embedding_model = getattr(langchain_community.embeddings, text_embedding_model, None)
            if embedding_model:
                self.assert_wrapped(embedding_model.embed_query)
                self.assert_wrapped(embedding_model.embed_documents)
        for vectorstore in vectorstore_classes:
            vectorstore_interface = getattr(langchain_community.vectorstores, vectorstore, None)
            if vectorstore_interface:
                self.assert_wrapped(vectorstore_interface.similarity_search)

    def assert_not_module_patched(self, langchain):
        try:
            import langchain_community
            from langchain_community import embeddings  # noqa: F401
            from langchain_community import vectorstores  # noqa: F401
        except ImportError:
            langchain_community = None

        import langchain_openai
        import langchain_pinecone

        self.assert_not_wrapped(langchain_openai.OpenAIEmbeddings.embed_documents)
        self.assert_not_wrapped(langchain_pinecone.PineconeVectorStore.similarity_search)

        if not langchain_community:
            return
        for text_embedding_model in text_embedding_models:
            embedding_model = getattr(langchain_community.embeddings, text_embedding_model, None)
            if embedding_model:
                self.assert_not_wrapped(embedding_model.embed_query)
                self.assert_not_wrapped(embedding_model.embed_documents)
        for vectorstore in vectorstore_classes:
            vectorstore_interface = getattr(langchain_community.vectorstores, vectorstore, None)
            if vectorstore_interface:
                self.assert_not_wrapped(vectorstore_interface.similarity_search)

    def assert_not_module_double_patched(self, langchain):
        try:
            import langchain_community  # noqa: F401
        except ImportError:
            langchain_community = None

        import langchain_openai
        import langchain_pinecone

        self.assert_not_double_wrapped(langchain_openai.OpenAIEmbeddings.embed_documents)
        self.assert_not_double_wrapped(langchain_pinecone.PineconeVectorStore.similarity_search)

        if not langchain_community:
            return
        for text_embedding_model in text_embedding_models:
            embedding_model = getattr(langchain_community.embeddings, text_embedding_model, None)
            if embedding_model:
                self.assert_not_double_wrapped(embedding_model.embed_query)
                self.assert_not_double_wrapped(embedding_model.embed_documents)
        for vectorstore in vectorstore_classes:
            vectorstore_interface = getattr(langchain_community.vectorstores, vectorstore, None)
            if vectorstore_interface:
                self.assert_not_double_wrapped(vectorstore_interface.similarity_search)
