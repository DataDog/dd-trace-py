import os
import sys
from typing import Any
from typing import Dict
from typing import Optional

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


try:
    from langchain.callbacks.openai_info import get_openai_token_cost_for_model
except ImportError:
    try:
        from langchain_community.callbacks.openai_info import get_openai_token_cost_for_model
    except ImportError:
        get_openai_token_cost_for_model = None

import wrapt

from ddtrace import config
from ddtrace.contrib.internal.langchain.constants import text_embedding_models
from ddtrace.contrib.internal.langchain.constants import vectorstore_classes
from ddtrace.contrib.internal.langchain.utils import shared_stream
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import with_traced_module
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import ArgumentError
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import deep_getattr
from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._integrations import LangChainIntegration
from ddtrace.llmobs._utils import safe_json
from ddtrace.trace import Pin
from ddtrace.trace import Span


log = get_logger(__name__)


def get_version():
    # type: () -> str
    return getattr(langchain, "__version__", "")


config._add(
    "langchain",
    {
        "span_char_limit": int(os.getenv("DD_LANGCHAIN_SPAN_CHAR_LIMIT", 128)),
    },
)


def _supported_versions() -> Dict[str, str]:
    return {"langchain": ">=0.1"}


def _extract_model_name(instance: Any) -> Optional[str]:
    """Extract model name or ID from llm instance."""
    for attr in ("model", "model_name", "model_id", "model_key", "repo_id"):
        if hasattr(instance, attr):
            return getattr(instance, attr)
    return None


@with_traced_module
def traced_llm_generate(langchain, pin, func, instance, args, kwargs):
    llm_provider = instance._llm_type
    integration = langchain._datadog_integration
    model = _extract_model_name(instance)
    prompts = get_argument_value(args, kwargs, 0, "prompts")
    span = integration.trace(
        pin,
        "%s.%s" % (instance.__module__, instance.__class__.__name__),
        submit_to_llmobs=True,
        interface_type="llm",
        provider=llm_provider,
        model=model,
        instance=instance,
    )
    completions = None

    integration.record_instance(instance, span)

    try:
        completions = func(*args, **kwargs)
        core.dispatch("langchain.llm.generate.after", (prompts, completions))
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        kwargs["_dd.identifying_params"] = instance._identifying_params
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=completions, operation="llm")
        span.finish()
    return completions


@with_traced_module
async def traced_llm_agenerate(langchain, pin, func, instance, args, kwargs):
    llm_provider = instance._llm_type
    prompts = get_argument_value(args, kwargs, 0, "prompts")
    integration = langchain._datadog_integration
    model = _extract_model_name(instance)
    span = integration.trace(
        pin,
        "%s.%s" % (instance.__module__, instance.__class__.__name__),
        submit_to_llmobs=True,
        interface_type="llm",
        provider=llm_provider,
        model=model,
        instance=instance,
    )

    integration.record_instance(instance, span)

    completions = None
    try:
        completions = await func(*args, **kwargs)
        core.dispatch("langchain.llm.agenerate.after", (prompts, completions))
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        kwargs["_dd.identifying_params"] = instance._identifying_params
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=completions, operation="llm")
        span.finish()
    return completions


@with_traced_module
def traced_chat_model_generate(langchain, pin, func, instance, args, kwargs):
    llm_provider = instance._llm_type.split("-")[0]
    chat_messages = get_argument_value(args, kwargs, 0, "messages")
    integration = langchain._datadog_integration
    span = integration.trace(
        pin,
        "%s.%s" % (instance.__module__, instance.__class__.__name__),
        submit_to_llmobs=True,
        interface_type="chat_model",
        provider=llm_provider,
        model=_extract_model_name(instance),
        instance=instance,
    )

    integration.record_instance(instance, span)

    chat_completions = None
    try:
        chat_completions = func(*args, **kwargs)
        core.dispatch("langchain.chatmodel.generate.after", (chat_messages, chat_completions))
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        kwargs["_dd.identifying_params"] = instance._identifying_params
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=chat_completions, operation="chat")
        span.finish()
    return chat_completions


@with_traced_module
async def traced_chat_model_agenerate(langchain, pin, func, instance, args, kwargs):
    llm_provider = instance._llm_type.split("-")[0]
    chat_messages = get_argument_value(args, kwargs, 0, "messages")
    integration = langchain._datadog_integration
    span = integration.trace(
        pin,
        "%s.%s" % (instance.__module__, instance.__class__.__name__),
        submit_to_llmobs=True,
        interface_type="chat_model",
        provider=llm_provider,
        model=_extract_model_name(instance),
        instance=instance,
    )

    integration.record_instance(instance, span)

    chat_completions = None
    try:
        chat_completions = await func(*args, **kwargs)
        core.dispatch("langchain.chatmodel.agenerate.after", (chat_messages, chat_completions))
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        kwargs["_dd.identifying_params"] = instance._identifying_params
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=chat_completions, operation="chat")
        span.finish()
    return chat_completions


@with_traced_module
def traced_embedding(langchain, pin, func, instance, args, kwargs):
    """
    This traces both embed_query(text) and embed_documents(texts), so we need to make sure
    we get the right arg/kwarg.
    """
    provider = instance.__class__.__name__.split("Embeddings")[0].lower()
    integration = langchain._datadog_integration
    span = integration.trace(
        pin,
        "%s.%s" % (instance.__module__, instance.__class__.__name__),
        submit_to_llmobs=True,
        interface_type="embedding",
        provider=provider,
        model=_extract_model_name(instance),
        instance=instance,
    )

    integration.record_instance(instance, span)

    embeddings = None
    try:
        embeddings = func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=embeddings, operation="embedding")
        span.finish()
    return embeddings


@with_traced_module
def traced_lcel_runnable_sequence(langchain, pin, func, instance, args, kwargs):
    """
    Traces the top level call of a LangChain Expression Language (LCEL) chain.

    LCEL is a new way of chaining in LangChain. It works by piping the output of one step of a chain into the next.
    This is similar in concept to the legacy LLMChain class, but instead relies internally on the idea of a
    RunnableSequence. It uses the operator `|` to create an implicit chain of `Runnable` steps.

    It works with a set of useful tools that distill legacy ways of creating chains,
    and various tasks and tooling within, making it preferable to LLMChain and related classes.

    This method captures the initial inputs to the chain, as well as the final outputs, and tags them appropriately.
    """
    integration = langchain._datadog_integration
    span = integration.trace(
        pin,
        "{}.{}".format(instance.__module__, instance.__class__.__name__),
        submit_to_llmobs=True,
        interface_type="chain",
        instance=instance,
    )
    inputs = None
    final_output = None

    integration.record_instance(instance, span)

    try:
        try:
            inputs = get_argument_value(args, kwargs, 0, "input")
        except ArgumentError:
            inputs = get_argument_value(args, kwargs, 0, "inputs")
        if not isinstance(inputs, list):
            inputs = [inputs]
        final_output = func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        integration.llmobs_set_tags(span, args=[], kwargs=inputs, response=final_output, operation="chain")
        span.finish()
    return final_output


@with_traced_module
async def traced_lcel_runnable_sequence_async(langchain, pin, func, instance, args, kwargs):
    """
    Similar to `traced_lcel_runnable_sequence`, but for async chaining calls.
    """
    integration = langchain._datadog_integration
    span = integration.trace(
        pin,
        "{}.{}".format(instance.__module__, instance.__class__.__name__),
        submit_to_llmobs=True,
        interface_type="chain",
        instance=instance,
    )
    inputs = None
    final_output = None

    integration.record_instance(instance, span)

    try:
        try:
            inputs = get_argument_value(args, kwargs, 0, "input")
        except ArgumentError:
            inputs = get_argument_value(args, kwargs, 0, "inputs")
        if not isinstance(inputs, list):
            inputs = [inputs]
        final_output = await func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        integration.llmobs_set_tags(span, args=[], kwargs=inputs, response=final_output, operation="chain")
        span.finish()
    return final_output


@with_traced_module
def traced_similarity_search(langchain, pin, func, instance, args, kwargs):
    integration = langchain._datadog_integration
    provider = instance.__class__.__name__.lower()
    span = integration.trace(
        pin,
        "%s.%s" % (instance.__module__, instance.__class__.__name__),
        submit_to_llmobs=True,
        interface_type="similarity_search",
        provider=provider,
        instance=instance,
    )

    integration.record_instance(instance, span)

    documents = []
    try:
        documents = func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=documents, operation="retrieval")
        span.finish()
    return documents


@with_traced_module
def traced_chain_stream(langchain, pin, func, instance, args, kwargs):
    integration: LangChainIntegration = langchain._datadog_integration

    def _on_span_started(span: Span):
        integration.record_instance(instance, span)

    def _on_span_finished(span: Span, streamed_chunks):
        maybe_parser = instance.steps[-1] if instance.steps else None
        if (
            streamed_chunks
            and langchain_core
            and isinstance(maybe_parser, langchain_core.output_parsers.JsonOutputParser)
        ):
            # it's possible that the chain has a json output parser type
            # this will have already concatenated the chunks into an object

            # it's also possible the this parser type isn't the last step,
            # but one of the last steps, in which case we won't act on it here
            result = streamed_chunks[-1]
            if maybe_parser.__class__.__name__ == "JsonOutputParser":
                content = safe_json(result)
            else:
                content = str(result)
        else:
            # best effort to join chunks together
            content = "".join([str(chunk) for chunk in streamed_chunks])
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=content, operation="chain")

    return shared_stream(
        integration=integration,
        pin=pin,
        func=func,
        instance=instance,
        args=args,
        kwargs=kwargs,
        interface_type="chain",
        on_span_started=_on_span_started,
        on_span_finished=_on_span_finished,
    )


@with_traced_module
def traced_chat_stream(langchain, pin, func, instance, args, kwargs):
    integration: LangChainIntegration = langchain._datadog_integration
    llm_provider = instance._llm_type
    model = _extract_model_name(instance)

    def _on_span_started(span: Span):
        integration.record_instance(instance, span)

    def _on_span_finished(span: Span, streamed_chunks):
        joined_chunks = streamed_chunks[0]
        for chunk in streamed_chunks[1:]:
            joined_chunks += chunk  # base message types support __add__ for concatenation
        kwargs["_dd.identifying_params"] = instance._identifying_params
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=joined_chunks, operation="chat")

    return shared_stream(
        integration=integration,
        pin=pin,
        func=func,
        instance=instance,
        args=args,
        kwargs=kwargs,
        interface_type="chat_model",
        on_span_started=_on_span_started,
        on_span_finished=_on_span_finished,
        provider=llm_provider,
        model=model,
    )


@with_traced_module
def traced_llm_stream(langchain, pin, func, instance, args, kwargs):
    integration: LangChainIntegration = langchain._datadog_integration
    llm_provider = instance._llm_type
    model = _extract_model_name(instance)

    def _on_span_start(span: Span):
        integration.record_instance(instance, span)

    def _on_span_finished(span: Span, streamed_chunks):
        content = "".join([str(chunk) for chunk in streamed_chunks])
        kwargs["_dd.identifying_params"] = instance._identifying_params
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=content, operation="llm")

    return shared_stream(
        integration=integration,
        pin=pin,
        func=func,
        instance=instance,
        args=args,
        kwargs=kwargs,
        interface_type="llm",
        on_span_started=_on_span_start,
        on_span_finished=_on_span_finished,
        provider=llm_provider,
        model=model,
    )


@with_traced_module
def traced_base_tool_invoke(langchain, pin, func, instance, args, kwargs):
    integration = langchain._datadog_integration
    tool_input = get_argument_value(args, kwargs, 0, "input")
    config = get_argument_value(args, kwargs, 1, "config", optional=True)

    span = integration.trace(
        pin,
        "%s" % func.__self__.name,
        interface_type="tool",
        submit_to_llmobs=True,
        instance=instance,
    )

    integration.record_instance(instance, span)

    tool_output = None
    tool_info = {}
    try:
        for attribute in ("name", "description"):
            value = getattr(instance, attribute, None)
            tool_info[attribute] = value

        metadata = getattr(instance, "metadata", {})
        if metadata:
            tool_info["metadata"] = metadata
        tags = getattr(instance, "tags", [])
        if tags:
            tool_info["tags"] = tags

        tool_output = func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        tool_inputs = {"input": tool_input, "config": config or {}, "info": tool_info or {}}
        integration.llmobs_set_tags(span, args=[], kwargs=tool_inputs, response=tool_output, operation="tool")
        span.finish()
    return tool_output


@with_traced_module
async def traced_base_tool_ainvoke(langchain, pin, func, instance, args, kwargs):
    integration = langchain._datadog_integration
    tool_input = get_argument_value(args, kwargs, 0, "input")
    config = get_argument_value(args, kwargs, 1, "config", optional=True)

    span = integration.trace(
        pin,
        "%s" % func.__self__.name,
        interface_type="tool",
        submit_to_llmobs=True,
        instance=instance,
    )

    integration.record_instance(instance, span)

    tool_output = None
    tool_info = {}
    try:
        for attribute in ("name", "description"):
            value = getattr(instance, attribute, None)
            tool_info[attribute] = value

        metadata = getattr(instance, "metadata", {})
        if metadata:
            tool_info["metadata"] = metadata
        tags = getattr(instance, "tags", [])
        if tags:
            tool_info["tags"] = tags

        tool_output = await func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        tool_inputs = {"input": tool_input, "config": config or {}, "info": tool_info or {}}
        integration.llmobs_set_tags(span, args=[], kwargs=tool_inputs, response=tool_output, operation="tool")
        span.finish()
    return tool_output


def _patch_embeddings_and_vectorstores():
    """
    Text embedding models override two abstract base methods instead of super calls,
    so we need to wrap each langchain-provided text embedding and vectorstore model.
    """
    if langchain_community is None:
        return

    from langchain_community import embeddings  # noqa:F401
    from langchain_community import vectorstores  # noqa:F401

    for text_embedding_model in text_embedding_models:
        if hasattr(langchain_community.embeddings, text_embedding_model):
            # Ensure not double patched, as some Embeddings interfaces are pointers to other Embeddings.
            if not isinstance(
                deep_getattr(langchain_community.embeddings, "%s.embed_query" % text_embedding_model),
                wrapt.ObjectProxy,
            ):
                wrap(
                    langchain_community.__name__,
                    "embeddings.%s.embed_query" % text_embedding_model,
                    traced_embedding(langchain),
                )
            if not isinstance(
                deep_getattr(langchain_community.embeddings, "%s.embed_documents" % text_embedding_model),
                wrapt.ObjectProxy,
            ):
                wrap(
                    langchain_community.__name__,
                    "embeddings.%s.embed_documents" % text_embedding_model,
                    traced_embedding(langchain),
                )
    for vectorstore in vectorstore_classes:
        if hasattr(langchain_community.vectorstores, vectorstore):
            # Ensure not double patched, as some Embeddings interfaces are pointers to other Embeddings.
            if not isinstance(
                deep_getattr(langchain_community.vectorstores, "%s.similarity_search" % vectorstore),
                wrapt.ObjectProxy,
            ):
                wrap(
                    langchain_community.__name__,
                    "vectorstores.%s.similarity_search" % vectorstore,
                    traced_similarity_search(langchain),
                )


def _unpatch_embeddings_and_vectorstores():
    """
    Text embedding models override two abstract base methods instead of super calls,
    so we need to unwrap each langchain-provided text embedding and vectorstore model.
    """
    if langchain_community is None:
        return

    for text_embedding_model in text_embedding_models:
        if hasattr(langchain_community.embeddings, text_embedding_model):
            if isinstance(
                deep_getattr(langchain_community.embeddings, "%s.embed_query" % text_embedding_model),
                wrapt.ObjectProxy,
            ):
                unwrap(getattr(langchain_community.embeddings, text_embedding_model), "embed_query")
            if isinstance(
                deep_getattr(langchain_community.embeddings, "%s.embed_documents" % text_embedding_model),
                wrapt.ObjectProxy,
            ):
                unwrap(getattr(langchain_community.embeddings, text_embedding_model), "embed_documents")
    for vectorstore in vectorstore_classes:
        if hasattr(langchain_community.vectorstores, vectorstore):
            if isinstance(
                deep_getattr(langchain_community.vectorstores, "%s.similarity_search" % vectorstore),
                wrapt.ObjectProxy,
            ):
                unwrap(getattr(langchain_community.vectorstores, vectorstore), "similarity_search")


def patch():
    if getattr(langchain, "_datadog_patch", False):
        return

    version = parse_version(get_version())
    if parse_version(get_version()) < (0, 1, 0):
        log.warning("langchain version %s is not supported, please upgrade to langchain version 0.1 or later", version)
        return

    langchain._datadog_patch = True

    Pin().onto(langchain)
    integration = LangChainIntegration(integration_config=config.langchain)
    langchain._datadog_integration = integration

    # Langchain doesn't allow wrapping directly from root, so we have to import the base classes first before wrapping.
    # ref: https://github.com/DataDog/dd-trace-py/issues/7123
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
    wrap("langchain_core", "runnables.base.RunnableSequence.invoke", traced_lcel_runnable_sequence(langchain))
    wrap("langchain_core", "runnables.base.RunnableSequence.ainvoke", traced_lcel_runnable_sequence_async(langchain))
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

    if langchain_community:
        _patch_embeddings_and_vectorstores()

    core.dispatch("langchain.patch", tuple())


def unpatch():
    if not getattr(langchain, "_datadog_patch", False):
        return

    langchain._datadog_patch = False

    unwrap(langchain_core.language_models.llms.BaseLLM, "generate")
    unwrap(langchain_core.language_models.llms.BaseLLM, "agenerate")
    unwrap(langchain_core.language_models.chat_models.BaseChatModel, "generate")
    unwrap(langchain_core.language_models.chat_models.BaseChatModel, "agenerate")
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

    if langchain_community:
        _unpatch_embeddings_and_vectorstores()

    core.dispatch("langchain.unpatch", tuple())

    delattr(langchain, "_datadog_integration")
