from ddtrace.contrib.internal.langchain_openai.patch import get_version
from ddtrace.contrib.internal.langchain_openai.patch import patch
from ddtrace.contrib.internal.langchain_openai.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestLangchainPatch(PatchTestCase.Base):
    __integration_name__ = "langchain_openai"
    __module_name__ = "langchain_openai"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, langchain_openai):
        import langchain_openai

        self.assert_wrapped(langchain_openai.OpenAIEmbeddings.embed_documents)
        self.assert_wrapped(langchain_openai.OpenAIEmbeddings.embed_query)

    def assert_not_module_patched(self, langchain_openai):
        import langchain_openai

        self.assert_not_wrapped(langchain_openai.OpenAIEmbeddings.embed_documents)
        self.assert_not_wrapped(langchain_openai.OpenAIEmbeddings.embed_query)

    def assert_not_module_double_patched(self, langchain_openai):
        import langchain_openai

        self.assert_not_double_wrapped(langchain_openai.OpenAIEmbeddings.embed_documents)
        self.assert_not_double_wrapped(langchain_openai.OpenAIEmbeddings.embed_query)
