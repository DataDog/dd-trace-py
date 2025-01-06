import os
import sys

import langchain

try:
    import langchain_core
except ImportError:
    langchain_core = None
try:
    import langchain_community
except ImportError:
    langchain_community = None
try:
    import langchain_openai
except ImportError:
    langchain_openai = None
try:
    import langchain_pinecone
except ImportError:
    langchain_pinecone = None

import wrapt

from ddtrace import config
from ddtrace.appsec._iast import _is_iast_enabled
from ddtrace.contrib.internal.langchain.constants import agent_output_parser_classes
from ddtrace.contrib.internal.langchain.constants import text_embedding_models
from ddtrace.contrib.internal.langchain.constants import vectorstore_classes
from ddtrace.contrib.internal.langchain.llms import traced_llm_agenerate
from ddtrace.contrib.internal.langchain.llms import traced_llm_generate
from ddtrace.contrib.internal.langchain.llms import traced_chat_model_generate
from ddtrace.contrib.internal.langchain.llms import traced_chat_model_agenerate
from ddtrace.contrib.internal.langchain.embeddings import traced_embedding
from ddtrace.contrib.internal.langchain.stream import traced_llm_stream
from ddtrace.contrib.internal.langchain.stream import traced_chat_stream
from ddtrace.contrib.internal.langchain.stream import traced_chain_stream
from ddtrace.contrib.internal.langchain.tools import traced_base_tool_invoke
from ddtrace.contrib.internal.langchain.tools import traced_base_tool_ainvoke
from ddtrace.contrib.internal.langchain.vectorstores import traced_similarity_search
from ddtrace.contrib.internal.langchain.chains import traced_lcel_runnable_sequence
from ddtrace.contrib.internal.langchain.chains import traced_lcel_runnable_sequence_async
from ddtrace.contrib.internal.langchain.chains import traced_chain_call
from ddtrace.contrib.internal.langchain.chains import traced_chain_acall
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.formats import deep_getattr
from ddtrace.contrib.internal.langchain.utils import get_version  # noqa: F401
from ddtrace.contrib.internal.langchain.utils import PATCH_LANGCHAIN_V0

from ddtrace.llmobs._integrations import LangChainIntegration
from ddtrace.pin import Pin


log = get_logger(__name__)


config._add(
    "langchain",
    {
        "logs_enabled": asbool(os.getenv("DD_LANGCHAIN_LOGS_ENABLED", False)),
        "metrics_enabled": asbool(os.getenv("DD_LANGCHAIN_METRICS_ENABLED", True)),
        "span_prompt_completion_sample_rate": float(os.getenv("DD_LANGCHAIN_SPAN_PROMPT_COMPLETION_SAMPLE_RATE", 1.0)),
        "log_prompt_completion_sample_rate": float(os.getenv("DD_LANGCHAIN_LOG_PROMPT_COMPLETION_SAMPLE_RATE", 0.1)),
        "span_char_limit": int(os.getenv("DD_LANGCHAIN_SPAN_CHAR_LIMIT", 128)),
    },
)


def _patch_embeddings_and_vectorstores():
    """
    Text embedding models override two abstract base methods instead of super calls,
    so we need to wrap each langchain-provided text embedding and vectorstore model.
    """
    base_langchain_module = langchain
    if not PATCH_LANGCHAIN_V0 and langchain_community:
        from langchain_community import embeddings  # noqa:F401
        from langchain_community import vectorstores  # noqa:F401

        base_langchain_module = langchain_community
    if not PATCH_LANGCHAIN_V0 and langchain_community is None:
        return
    for text_embedding_model in text_embedding_models:
        if hasattr(base_langchain_module.embeddings, text_embedding_model):
            # Ensure not double patched, as some Embeddings interfaces are pointers to other Embeddings.
            if not isinstance(
                deep_getattr(base_langchain_module.embeddings, "%s.embed_query" % text_embedding_model),
                wrapt.ObjectProxy,
            ):
                wrap(
                    base_langchain_module.__name__,
                    "embeddings.%s.embed_query" % text_embedding_model,
                    traced_embedding(langchain),
                )
            if not isinstance(
                deep_getattr(base_langchain_module.embeddings, "%s.embed_documents" % text_embedding_model),
                wrapt.ObjectProxy,
            ):
                wrap(
                    base_langchain_module.__name__,
                    "embeddings.%s.embed_documents" % text_embedding_model,
                    traced_embedding(langchain),
                )
    for vectorstore in vectorstore_classes:
        if hasattr(base_langchain_module.vectorstores, vectorstore):
            # Ensure not double patched, as some Embeddings interfaces are pointers to other Embeddings.
            if not isinstance(
                deep_getattr(base_langchain_module.vectorstores, "%s.similarity_search" % vectorstore),
                wrapt.ObjectProxy,
            ):
                wrap(
                    base_langchain_module.__name__,
                    "vectorstores.%s.similarity_search" % vectorstore,
                    traced_similarity_search(langchain),
                )


def _unpatch_embeddings_and_vectorstores():
    """
    Text embedding models override two abstract base methods instead of super calls,
    so we need to unwrap each langchain-provided text embedding and vectorstore model.
    """
    base_langchain_module = langchain if PATCH_LANGCHAIN_V0 else langchain_community
    if not PATCH_LANGCHAIN_V0 and langchain_community is None:
        return
    for text_embedding_model in text_embedding_models:
        if hasattr(base_langchain_module.embeddings, text_embedding_model):
            if isinstance(
                deep_getattr(base_langchain_module.embeddings, "%s.embed_query" % text_embedding_model),
                wrapt.ObjectProxy,
            ):
                unwrap(getattr(base_langchain_module.embeddings, text_embedding_model), "embed_query")
            if isinstance(
                deep_getattr(base_langchain_module.embeddings, "%s.embed_documents" % text_embedding_model),
                wrapt.ObjectProxy,
            ):
                unwrap(getattr(base_langchain_module.embeddings, text_embedding_model), "embed_documents")
    for vectorstore in vectorstore_classes:
        if hasattr(base_langchain_module.vectorstores, vectorstore):
            if isinstance(
                deep_getattr(base_langchain_module.vectorstores, "%s.similarity_search" % vectorstore),
                wrapt.ObjectProxy,
            ):
                unwrap(getattr(base_langchain_module.vectorstores, vectorstore), "similarity_search")


def patch():
    if getattr(langchain, "_datadog_patch", False):
        return

    langchain._datadog_patch = True

    Pin().onto(langchain)
    integration = LangChainIntegration(integration_config=config.langchain)
    langchain._datadog_integration = integration

    # Langchain doesn't allow wrapping directly from root, so we have to import the base classes first before wrapping.
    # ref: https://github.com/DataDog/dd-trace-py/issues/7123
    if PATCH_LANGCHAIN_V0:
        from langchain import embeddings  # noqa:F401
        from langchain import vectorstores  # noqa:F401
        from langchain.chains.base import Chain  # noqa:F401
        from langchain.chat_models.base import BaseChatModel  # noqa:F401
        from langchain.llms.base import BaseLLM  # noqa:F401

        wrap("langchain", "llms.base.BaseLLM.generate", traced_llm_generate(langchain))
        wrap("langchain", "llms.base.BaseLLM.agenerate", traced_llm_agenerate(langchain))
        wrap("langchain", "chat_models.base.BaseChatModel.generate", traced_chat_model_generate(langchain))
        wrap("langchain", "chat_models.base.BaseChatModel.agenerate", traced_chat_model_agenerate(langchain))
        wrap("langchain", "chains.base.Chain.__call__", traced_chain_call(langchain))
        wrap("langchain", "chains.base.Chain.acall", traced_chain_acall(langchain))
        wrap("langchain", "embeddings.OpenAIEmbeddings.embed_query", traced_embedding(langchain))
        wrap("langchain", "embeddings.OpenAIEmbeddings.embed_documents", traced_embedding(langchain))
    else:
        from langchain.chains.base import Chain  # noqa:F401
        from langchain_core.tools import BaseTool  # noqa:F401

        wrap("langchain_core", "language_models.llms.BaseLLM.generate", traced_llm_generate(langchain))
        wrap("langchain_core", "language_models.llms.BaseLLM.agenerate", traced_llm_agenerate(langchain))
        wrap(
            "langchain_core",
            "language_models.chat_models.BaseChatModel.generate",
            traced_chat_model_generate(langchain),
        )
        wrap(
            "langchain_core",
            "language_models.chat_models.BaseChatModel.agenerate",
            traced_chat_model_agenerate(langchain),
        )
        wrap("langchain", "chains.base.Chain.invoke", traced_chain_call(langchain))
        wrap("langchain", "chains.base.Chain.ainvoke", traced_chain_acall(langchain))
        wrap("langchain_core", "runnables.base.RunnableSequence.invoke", traced_lcel_runnable_sequence(langchain))
        wrap(
            "langchain_core", "runnables.base.RunnableSequence.ainvoke", traced_lcel_runnable_sequence_async(langchain)
        )
        wrap("langchain_core", "runnables.base.RunnableSequence.batch", traced_lcel_runnable_sequence(langchain))
        wrap("langchain_core", "runnables.base.RunnableSequence.abatch", traced_lcel_runnable_sequence_async(langchain))
        wrap("langchain_core", "runnables.base.RunnableSequence.stream", traced_chain_stream(langchain))
        wrap("langchain_core", "runnables.base.RunnableSequence.astream", traced_chain_stream(langchain))
        wrap(
            "langchain_core",
            "language_models.chat_models.BaseChatModel.stream",
            traced_chat_stream(langchain),
        )
        wrap(
            "langchain_core",
            "language_models.chat_models.BaseChatModel.astream",
            traced_chat_stream(langchain),
        )
        wrap("langchain_core", "language_models.llms.BaseLLM.stream", traced_llm_stream(langchain))
        wrap("langchain_core", "language_models.llms.BaseLLM.astream", traced_llm_stream(langchain))

        wrap("langchain_core", "tools.BaseTool.invoke", traced_base_tool_invoke(langchain))
        wrap("langchain_core", "tools.BaseTool.ainvoke", traced_base_tool_ainvoke(langchain))
        if langchain_openai:
            wrap("langchain_openai", "OpenAIEmbeddings.embed_documents", traced_embedding(langchain))
        if langchain_pinecone:
            wrap("langchain_pinecone", "PineconeVectorStore.similarity_search", traced_similarity_search(langchain))

    if PATCH_LANGCHAIN_V0 or langchain_community:
        _patch_embeddings_and_vectorstores()

    if _is_iast_enabled():
        from ddtrace.appsec._iast._metrics import _set_iast_error_metric

        def wrap_output_parser(module, parser):
            # Ensure not double patched
            if not isinstance(deep_getattr(module, "%s.parse" % parser), wrapt.ObjectProxy):
                wrap(module, "%s.parse" % parser, taint_parser_output)

        try:
            with_agent_output_parser(wrap_output_parser)
        except Exception as e:
            _set_iast_error_metric("IAST propagation error. langchain wrap_output_parser. {}".format(e))


def unpatch():
    if not getattr(langchain, "_datadog_patch", False):
        return

    langchain._datadog_patch = False

    if PATCH_LANGCHAIN_V0:
        unwrap(langchain.llms.base.BaseLLM, "generate")
        unwrap(langchain.llms.base.BaseLLM, "agenerate")
        unwrap(langchain.chat_models.base.BaseChatModel, "generate")
        unwrap(langchain.chat_models.base.BaseChatModel, "agenerate")
        unwrap(langchain.chains.base.Chain, "__call__")
        unwrap(langchain.chains.base.Chain, "acall")
        unwrap(langchain.embeddings.OpenAIEmbeddings, "embed_query")
        unwrap(langchain.embeddings.OpenAIEmbeddings, "embed_documents")
    else:
        unwrap(langchain_core.language_models.llms.BaseLLM, "generate")
        unwrap(langchain_core.language_models.llms.BaseLLM, "agenerate")
        unwrap(langchain_core.language_models.chat_models.BaseChatModel, "generate")
        unwrap(langchain_core.language_models.chat_models.BaseChatModel, "agenerate")
        unwrap(langchain.chains.base.Chain, "invoke")
        unwrap(langchain.chains.base.Chain, "ainvoke")
        unwrap(langchain_core.runnables.base.RunnableSequence, "invoke")
        unwrap(langchain_core.runnables.base.RunnableSequence, "ainvoke")
        unwrap(langchain_core.runnables.base.RunnableSequence, "batch")
        unwrap(langchain_core.runnables.base.RunnableSequence, "abatch")
        unwrap(langchain_core.runnables.base.RunnableSequence, "stream")
        unwrap(langchain_core.runnables.base.RunnableSequence, "astream")
        unwrap(langchain_core.language_models.chat_models.BaseChatModel, "stream")
        unwrap(langchain_core.language_models.chat_models.BaseChatModel, "astream")
        unwrap(langchain_core.language_models.llms.BaseLLM, "stream")
        unwrap(langchain_core.language_models.llms.BaseLLM, "astream")
        unwrap(langchain_core.tools.BaseTool, "invoke")
        unwrap(langchain_core.tools.BaseTool, "ainvoke")
        if langchain_openai:
            unwrap(langchain_openai.OpenAIEmbeddings, "embed_documents")
        if langchain_pinecone:
            unwrap(langchain_pinecone.PineconeVectorStore, "similarity_search")

    if PATCH_LANGCHAIN_V0 or langchain_community:
        _unpatch_embeddings_and_vectorstores()

    delattr(langchain, "_datadog_integration")


def taint_parser_output(func, instance, args, kwargs):
    from ddtrace.appsec._iast._metrics import _set_iast_error_metric
    from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges
    from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject

    result = func(*args, **kwargs)
    try:
        try:
            from langchain_core.agents import AgentAction
            from langchain_core.agents import AgentFinish
        except ImportError:
            from langchain.agents import AgentAction
            from langchain.agents import AgentFinish
        ranges = get_tainted_ranges(args[0])
        if ranges:
            source = ranges[0].source
            if isinstance(result, AgentAction):
                result.tool_input = taint_pyobject(result.tool_input, source.name, source.value, source.origin)
            elif isinstance(result, AgentFinish) and "output" in result.return_values:
                values = result.return_values
                values["output"] = taint_pyobject(values["output"], source.name, source.value, source.origin)
    except Exception as e:
        _set_iast_error_metric("IAST propagation error. langchain taint_parser_output. {}".format(e))

    return result


def with_agent_output_parser(f):
    import langchain.agents

    queue = [(langchain.agents, agent_output_parser_classes)]

    while len(queue) > 0:
        module, current = queue.pop(0)
        if isinstance(current, str):
            if hasattr(module, current):
                f(module, current)
        elif isinstance(current, dict):
            for name, value in current.items():
                if hasattr(module, name):
                    queue.append((getattr(module, name), value))
