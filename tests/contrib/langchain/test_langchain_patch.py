from ddtrace.contrib.internal.langchain.patch import _extract_model_name
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
        from langchain_core.runnables.base import RunnableLambda  # noqa: F401
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
        self.assert_wrapped(langchain_core.runnables.base.RunnableLambda.invoke)
        self.assert_wrapped(langchain_core.runnables.base.RunnableLambda.ainvoke)
        self.assert_wrapped(langchain_core.runnables.base.RunnableLambda.batch)
        self.assert_wrapped(langchain_core.runnables.base.RunnableLambda.abatch)
        self.assert_wrapped(langchain_core.embeddings.Embeddings.__init_subclass__)
        self.assert_wrapped(langchain_core.vectorstores.VectorStore.__init_subclass__)

    def assert_not_module_patched(self, langchain):
        import langchain_core
        from langchain_core.embeddings import Embeddings  # noqa: F401
        from langchain_core.language_models.chat_models import BaseChatModel  # noqa: F401
        from langchain_core.language_models.llms import BaseLLM  # noqa: F401
        from langchain_core.prompts.base import BasePromptTemplate  # noqa: F401
        from langchain_core.runnables.base import RunnableLambda  # noqa: F401
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
        self.assert_not_wrapped(langchain_core.runnables.base.RunnableLambda.invoke)
        self.assert_not_wrapped(langchain_core.runnables.base.RunnableLambda.ainvoke)
        self.assert_not_wrapped(langchain_core.runnables.base.RunnableLambda.batch)
        self.assert_not_wrapped(langchain_core.runnables.base.RunnableLambda.abatch)
        self.assert_not_wrapped(langchain_core.embeddings.Embeddings.__init_subclass__)
        self.assert_not_wrapped(langchain_core.vectorstores.VectorStore.__init_subclass__)

    def assert_not_module_double_patched(self, langchain):
        import langchain_core
        from langchain_core.embeddings import Embeddings  # noqa: F401
        from langchain_core.language_models.chat_models import BaseChatModel  # noqa: F401
        from langchain_core.language_models.llms import BaseLLM  # noqa: F401
        from langchain_core.prompts.base import BasePromptTemplate  # noqa: F401
        from langchain_core.runnables.base import RunnableLambda  # noqa: F401
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
        self.assert_not_double_wrapped(langchain_core.runnables.base.RunnableLambda.invoke)
        self.assert_not_double_wrapped(langchain_core.runnables.base.RunnableLambda.ainvoke)
        self.assert_not_double_wrapped(langchain_core.runnables.base.RunnableLambda.batch)
        self.assert_not_double_wrapped(langchain_core.runnables.base.RunnableLambda.abatch)
        self.assert_not_double_wrapped(langchain_core.embeddings.Embeddings.__init_subclass__)
        self.assert_not_double_wrapped(langchain_core.vectorstores.VectorStore.__init_subclass__)


class _FakeInstance:
    """Minimal stand-in for a langchain LLM/chat-model instance."""

    def __init__(self, **attrs):
        for name, value in attrs.items():
            setattr(self, name, value)


def test_extract_model_name_uses_model_id_when_no_base_model_id():
    instance = _FakeInstance(model_id="anthropic.claude-3-5-sonnet-20240620-v1:0")
    assert _extract_model_name(instance) == "anthropic.claude-3-5-sonnet-20240620-v1:0"


def test_extract_model_name_prefers_base_model_id_over_model_id():
    # ChatBedrockConverse with an inference profile: model_id is the profile
    # ARN, base_model_id names the underlying foundation model.
    instance = _FakeInstance(
        model_id="arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/my-profile",
        base_model_id="anthropic.claude-3-5-sonnet-20240620-v1:0",
    )
    assert _extract_model_name(instance) == "anthropic.claude-3-5-sonnet-20240620-v1:0"


def test_extract_model_name_falls_through_when_base_model_id_is_none():
    instance = _FakeInstance(
        base_model_id=None,
        model_id="anthropic.claude-3-5-sonnet-20240620-v1:0",
    )
    assert _extract_model_name(instance) == "anthropic.claude-3-5-sonnet-20240620-v1:0"


def test_extract_model_name_falls_through_when_base_model_id_is_empty_string():
    instance = _FakeInstance(
        base_model_id="",
        model_id="anthropic.claude-3-5-sonnet-20240620-v1:0",
    )
    assert _extract_model_name(instance) == "anthropic.claude-3-5-sonnet-20240620-v1:0"


def test_extract_model_name_strips_path_prefix():
    instance = _FakeInstance(model="models/gemini-2.5-flash")
    assert _extract_model_name(instance) == "gemini-2.5-flash"


def test_extract_model_name_returns_none_when_no_attrs_set():
    assert _extract_model_name(_FakeInstance()) is None
