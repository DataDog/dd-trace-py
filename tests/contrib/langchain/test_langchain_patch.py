from ddtrace.contrib.internal.langchain.patch import get_version
from ddtrace.contrib.internal.langchain.patch import patch
from ddtrace.contrib.internal.langchain.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestLangchainPatch(PatchTestCase.Base):
    __integration_name__ = "langchain"
    __module_name__ = "langchain_core"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, langchain):
        import langchain_core
        from langchain_core.embeddings import Embeddings  # noqa: F401
        from langchain_core.language_models.chat_models import BaseChatModel  # noqa: F401
        from langchain_core.language_models.llms import BaseLLM  # noqa: F401
        from langchain_core.prompts.base import BasePromptTemplate  # noqa: F401
        from langchain_core.runnables.base import RunnableSequence  # noqa: F401
        from langchain_core.tools import BaseTool  # noqa: F401
        from langchain_core.vectorstores import VectorStore  # noqa: F401

        self.assert_wrapped(langchain_core.language_models.llms.BaseLLM.generate)
        self.assert_wrapped(langchain_core.language_models.llms.BaseLLM.agenerate)
        self.assert_wrapped(langchain_core.language_models.chat_models.BaseChatModel.generate)
        self.assert_wrapped(langchain_core.language_models.chat_models.BaseChatModel.agenerate)
        self.assert_wrapped(langchain_core.runnables.base.RunnableSequence.invoke)
        self.assert_wrapped(langchain_core.runnables.base.RunnableSequence.ainvoke)
        self.assert_wrapped(langchain_core.runnables.base.RunnableSequence.batch)
        self.assert_wrapped(langchain_core.runnables.base.RunnableSequence.abatch)
        self.assert_wrapped(langchain_core.embeddings.Embeddings.__init_subclass__)
        self.assert_wrapped(langchain_core.vectorstores.VectorStore.__init_subclass__)

    def assert_not_module_patched(self, langchain):
        import langchain_core
        from langchain_core.embeddings import Embeddings  # noqa: F401
        from langchain_core.language_models.chat_models import BaseChatModel  # noqa: F401
        from langchain_core.language_models.llms import BaseLLM  # noqa: F401
        from langchain_core.prompts.base import BasePromptTemplate  # noqa: F401
        from langchain_core.runnables.base import RunnableSequence  # noqa: F401
        from langchain_core.tools import BaseTool  # noqa: F401
        from langchain_core.vectorstores import VectorStore  # noqa: F401

        self.assert_not_wrapped(langchain_core.language_models.llms.BaseLLM.generate)
        self.assert_not_wrapped(langchain_core.language_models.llms.BaseLLM.agenerate)
        self.assert_not_wrapped(langchain_core.language_models.chat_models.BaseChatModel.generate)
        self.assert_not_wrapped(langchain_core.language_models.chat_models.BaseChatModel.agenerate)
        self.assert_not_wrapped(langchain_core.runnables.base.RunnableSequence.invoke)
        self.assert_not_wrapped(langchain_core.runnables.base.RunnableSequence.ainvoke)
        self.assert_not_wrapped(langchain_core.runnables.base.RunnableSequence.batch)
        self.assert_not_wrapped(langchain_core.runnables.base.RunnableSequence.abatch)
        self.assert_not_wrapped(langchain_core.embeddings.Embeddings.__init_subclass__)
        self.assert_not_wrapped(langchain_core.vectorstores.VectorStore.__init_subclass__)

    def assert_not_module_double_patched(self, langchain):
        import langchain_core
        from langchain_core.embeddings import Embeddings  # noqa: F401
        from langchain_core.language_models.chat_models import BaseChatModel  # noqa: F401
        from langchain_core.language_models.llms import BaseLLM  # noqa: F401
        from langchain_core.prompts.base import BasePromptTemplate  # noqa: F401
        from langchain_core.runnables.base import RunnableSequence  # noqa: F401
        from langchain_core.tools import BaseTool  # noqa: F401
        from langchain_core.vectorstores import VectorStore  # noqa: F401

        self.assert_not_double_wrapped(langchain_core.language_models.llms.BaseLLM.generate)
        self.assert_not_double_wrapped(langchain_core.language_models.llms.BaseLLM.agenerate)
        self.assert_not_double_wrapped(langchain_core.language_models.chat_models.BaseChatModel.generate)
        self.assert_not_double_wrapped(langchain_core.language_models.chat_models.BaseChatModel.agenerate)
        self.assert_not_double_wrapped(langchain_core.runnables.base.RunnableSequence.invoke)
        self.assert_not_double_wrapped(langchain_core.runnables.base.RunnableSequence.ainvoke)
        self.assert_not_double_wrapped(langchain_core.runnables.base.RunnableSequence.batch)
        self.assert_not_double_wrapped(langchain_core.runnables.base.RunnableSequence.abatch)
        self.assert_not_double_wrapped(langchain_core.embeddings.Embeddings.__init_subclass__)
        self.assert_not_double_wrapped(langchain_core.vectorstores.VectorStore.__init_subclass__)
