from ddtrace.contrib.langchain import get_version
from ddtrace.contrib.langchain import patch
from ddtrace.contrib.langchain import unpatch
from ddtrace.contrib.langchain.constants import text_embedding_models
from ddtrace.contrib.langchain.constants import vectorstore_classes
from ddtrace.contrib.langchain.patch import PATCH_LANGCHAIN_V0
from tests.contrib.patch import PatchTestCase


class TestLangchainPatch(PatchTestCase.Base):
    __integration_name__ = "langchain"
    __module_name__ = "langchain"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, langchain):
        if PATCH_LANGCHAIN_V0:
            gated_langchain = langchain
            self.assert_wrapped(langchain.llms.base.BaseLLM.generate)
            self.assert_wrapped(langchain.llms.base.BaseLLM.agenerate)
            self.assert_wrapped(langchain.chat_models.base.BaseChatModel.generate)
            self.assert_wrapped(langchain.chat_models.base.BaseChatModel.agenerate)
            self.assert_wrapped(langchain.chains.base.Chain.__call__)
            self.assert_wrapped(langchain.chains.base.Chain.acall)
        else:
            try:
                import langchain_community as gated_langchain
            except ImportError:
                gated_langchain = None

            import langchain_core
            import langchain_openai
            import langchain_pinecone

            self.assert_wrapped(langchain_core.language_models.llms.BaseLLM.generate)
            self.assert_wrapped(langchain_core.language_models.llms.BaseLLM.agenerate)
            self.assert_wrapped(langchain_core.language_models.chat_models.BaseChatModel.generate)
            self.assert_wrapped(langchain_core.language_models.chat_models.BaseChatModel.agenerate)
            self.assert_wrapped(langchain.chains.base.Chain.invoke)
            self.assert_wrapped(langchain.chains.base.Chain.ainvoke)
            self.assert_wrapped(langchain_core.runnables.base.RunnableSequence.invoke)
            self.assert_wrapped(langchain_core.runnables.base.RunnableSequence.ainvoke)
            self.assert_wrapped(langchain_core.runnables.base.RunnableSequence.batch)
            self.assert_wrapped(langchain_core.runnables.base.RunnableSequence.abatch)
            self.assert_wrapped(langchain_openai.OpenAIEmbeddings.embed_documents)
            self.assert_wrapped(langchain_pinecone.PineconeVectorStore.similarity_search)

        if not gated_langchain:
            return
        for text_embedding_model in text_embedding_models:
            embedding_model = getattr(gated_langchain.embeddings, text_embedding_model, None)
            if embedding_model:
                self.assert_wrapped(embedding_model.embed_query)
                self.assert_wrapped(embedding_model.embed_documents)
        for vectorstore in vectorstore_classes:
            vectorstore_interface = getattr(gated_langchain.vectorstores, vectorstore, None)
            if vectorstore_interface:
                self.assert_wrapped(vectorstore_interface.similarity_search)

    def assert_not_module_patched(self, langchain):
        if PATCH_LANGCHAIN_V0:
            from langchain import embeddings  # noqa: F401
            from langchain import vectorstores  # noqa: F401

            gated_langchain = langchain
            self.assert_not_wrapped(langchain.llms.base.BaseLLM.generate)
            self.assert_not_wrapped(langchain.llms.base.BaseLLM.agenerate)
            self.assert_not_wrapped(langchain.chat_models.base.BaseChatModel.generate)
            self.assert_not_wrapped(langchain.chat_models.base.BaseChatModel.agenerate)
            self.assert_not_wrapped(langchain.chains.base.Chain.__call__)
            self.assert_not_wrapped(langchain.chains.base.Chain.acall)
        else:
            from langchain import chains  # noqa: F401
            from langchain.chains import base  # noqa: F401

            try:
                import langchain_community as gated_langchain
                from langchain_community import embeddings  # noqa: F401
                from langchain_community import vectorstores  # noqa: F401
            except ImportError:
                gated_langchain = None
            import langchain_core
            import langchain_openai
            import langchain_pinecone

            self.assert_not_wrapped(langchain_core.language_models.llms.BaseLLM.generate)
            self.assert_not_wrapped(langchain_core.language_models.llms.BaseLLM.agenerate)
            self.assert_not_wrapped(langchain_core.language_models.chat_models.BaseChatModel.generate)
            self.assert_not_wrapped(langchain_core.language_models.chat_models.BaseChatModel.agenerate)
            self.assert_not_wrapped(langchain.chains.base.Chain.invoke)
            self.assert_not_wrapped(langchain.chains.base.Chain.ainvoke)
            self.assert_not_wrapped(langchain_core.runnables.base.RunnableSequence.invoke)
            self.assert_not_wrapped(langchain_core.runnables.base.RunnableSequence.ainvoke)
            self.assert_not_wrapped(langchain_core.runnables.base.RunnableSequence.batch)
            self.assert_not_wrapped(langchain_core.runnables.base.RunnableSequence.abatch)
            self.assert_not_wrapped(langchain_openai.OpenAIEmbeddings.embed_documents)
            self.assert_not_wrapped(langchain_pinecone.PineconeVectorStore.similarity_search)

        if not gated_langchain:
            return
        for text_embedding_model in text_embedding_models:
            embedding_model = getattr(gated_langchain.embeddings, text_embedding_model, None)
            if embedding_model:
                self.assert_not_wrapped(embedding_model.embed_query)
                self.assert_not_wrapped(embedding_model.embed_documents)
        for vectorstore in vectorstore_classes:
            vectorstore_interface = getattr(gated_langchain.vectorstores, vectorstore, None)
            if vectorstore_interface:
                self.assert_not_wrapped(vectorstore_interface.similarity_search)

    def assert_not_module_double_patched(self, langchain):
        if PATCH_LANGCHAIN_V0:
            gated_langchain = langchain
            self.assert_not_double_wrapped(langchain.llms.base.BaseLLM.generate)
            self.assert_not_double_wrapped(langchain.llms.base.BaseLLM.agenerate)
            self.assert_not_double_wrapped(langchain.chat_models.base.BaseChatModel.generate)
            self.assert_not_double_wrapped(langchain.chat_models.base.BaseChatModel.agenerate)
            self.assert_not_double_wrapped(langchain.chains.base.Chain.__call__)
            self.assert_not_double_wrapped(langchain.chains.base.Chain.acall)
        else:
            from langchain.chains import base  # noqa: F401

            try:
                import langchain_community as gated_langchain
            except ImportError:
                gated_langchain = None
            import langchain_core
            import langchain_openai
            import langchain_pinecone

            self.assert_not_double_wrapped(langchain_core.language_models.llms.BaseLLM.generate)
            self.assert_not_double_wrapped(langchain_core.language_models.llms.BaseLLM.agenerate)
            self.assert_not_double_wrapped(langchain_core.language_models.chat_models.BaseChatModel.generate)
            self.assert_not_double_wrapped(langchain_core.language_models.chat_models.BaseChatModel.agenerate)
            self.assert_not_double_wrapped(langchain.chains.base.Chain.invoke)
            self.assert_not_double_wrapped(langchain.chains.base.Chain.ainvoke)
            self.assert_not_double_wrapped(langchain_core.runnables.base.RunnableSequence.invoke)
            self.assert_not_double_wrapped(langchain_core.runnables.base.RunnableSequence.ainvoke)
            self.assert_not_double_wrapped(langchain_core.runnables.base.RunnableSequence.batch)
            self.assert_not_double_wrapped(langchain_core.runnables.base.RunnableSequence.abatch)
            self.assert_not_double_wrapped(langchain_openai.OpenAIEmbeddings.embed_documents)
            self.assert_not_double_wrapped(langchain_pinecone.PineconeVectorStore.similarity_search)

        if not gated_langchain:
            return
        for text_embedding_model in text_embedding_models:
            embedding_model = getattr(gated_langchain.embeddings, text_embedding_model, None)
            if embedding_model:
                self.assert_not_double_wrapped(embedding_model.embed_query)
                self.assert_not_double_wrapped(embedding_model.embed_documents)
        for vectorstore in vectorstore_classes:
            vectorstore_interface = getattr(gated_langchain.vectorstores, vectorstore, None)
            if vectorstore_interface:
                self.assert_not_double_wrapped(vectorstore_interface.similarity_search)
