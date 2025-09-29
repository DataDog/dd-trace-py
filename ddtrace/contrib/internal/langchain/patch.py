import sys
from typing import Any
from typing import Dict
from typing import Optional
from typing import Tuple

import langchain_core
import wrapt

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib.internal.langchain.utils import shared_stream
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import with_traced_module
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import ArgumentError
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._integrations import LangChainIntegration
from ddtrace.llmobs._utils import safe_json
from ddtrace.trace import Span


log = get_logger(__name__)


def get_version() -> str:
    return getattr(langchain_core, "__version__", "")


def _supported_versions() -> Dict[str, str]:
    return {"langchain_core": ">=0.1"}


config._add("langchain", {})


def _extract_model_name(instance: Any) -> Optional[str]:
    """Extract model name or ID from llm instance."""
    for attr in ("model", "model_name", "model_id", "model_key", "repo_id"):
        if hasattr(instance, attr):
            return getattr(instance, attr)
    return None


def _raising_dispatch(event_id: str, args: Tuple[Any, ...] = ()):
    result = core.dispatch_with_results(event_id, args)
    if len(result) > 0:
        for event in result.values():
            # we explicitly set the exception as a value to prevent caught exceptions from leaking
            if isinstance(event.value, Exception):
                raise event.value


@with_traced_module
def traced_llm_generate(langchain_core, pin, func, instance, args, kwargs):
    llm_provider = instance._llm_type
    integration: LangChainIntegration = langchain_core._datadog_integration
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
    integration.llmobs_set_prompt_tag(instance, span)

    try:
        _raising_dispatch("langchain.llm.generate.before", (prompts,))
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
async def traced_llm_agenerate(langchain_core, pin, func, instance, args, kwargs):
    llm_provider = instance._llm_type
    prompts = get_argument_value(args, kwargs, 0, "prompts")
    integration: LangChainIntegration = langchain_core._datadog_integration
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
    integration.llmobs_set_prompt_tag(instance, span)

    completions = None
    try:
        _raising_dispatch("langchain.llm.agenerate.before", (prompts,))
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
def traced_chat_model_generate(langchain_core, pin, func, instance, args, kwargs):
    llm_provider = instance._llm_type.split("-")[0]
    chat_messages = get_argument_value(args, kwargs, 0, "messages")
    integration: LangChainIntegration = langchain_core._datadog_integration
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
    integration.llmobs_set_prompt_tag(instance, span)

    chat_completions = None
    try:
        _raising_dispatch("langchain.chatmodel.generate.before", (chat_messages,))
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
async def traced_chat_model_agenerate(langchain_core, pin, func, instance, args, kwargs):
    llm_provider = instance._llm_type.split("-")[0]
    chat_messages = get_argument_value(args, kwargs, 0, "messages")
    integration: LangChainIntegration = langchain_core._datadog_integration
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
    integration.llmobs_set_prompt_tag(instance, span)

    chat_completions = None
    try:
        _raising_dispatch("langchain.chatmodel.agenerate.before", (chat_messages,))
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
def traced_lcel_runnable_sequence(langchain_core, pin, func, instance, args, kwargs):
    """
    Traces the top level call of a LangChain Expression Language (LCEL) chain.

    LCEL is a new way of chaining in LangChain. It works by piping the output of one step of a chain into the next.
    This is similar in concept to the legacy LLMChain class, but instead relies internally on the idea of a
    RunnableSequence. It uses the operator `|` to create an implicit chain of `Runnable` steps.

    It works with a set of useful tools that distill legacy ways of creating chains,
    and various tasks and tooling within, making it preferable to LLMChain and related classes.

    This method captures the initial inputs to the chain, as well as the final outputs, and tags them appropriately.
    """
    integration: LangChainIntegration = langchain_core._datadog_integration
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
async def traced_lcel_runnable_sequence_async(langchain_core, pin, func, instance, args, kwargs):
    """
    Similar to `traced_lcel_runnable_sequence`, but for async chaining calls.
    """
    integration: LangChainIntegration = langchain_core._datadog_integration
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
def traced_chain_stream(langchain_core, pin, func, instance, args, kwargs):
    integration: LangChainIntegration = langchain_core._datadog_integration

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
def traced_chat_stream(langchain_core, pin, func, instance, args, kwargs):
    integration: LangChainIntegration = langchain_core._datadog_integration
    llm_provider = instance._llm_type
    model = _extract_model_name(instance)

    _raising_dispatch("langchain.chatmodel.stream.before", (instance, args, kwargs))

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
def traced_llm_stream(langchain_core, pin, func, instance, args, kwargs):
    integration: LangChainIntegration = langchain_core._datadog_integration
    llm_provider = instance._llm_type
    model = _extract_model_name(instance)

    _raising_dispatch(
        "langchain.llm.stream.before",
        (
            instance,
            args,
            kwargs,
        ),
    )

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
def traced_base_tool_invoke(langchain_core, pin, func, instance, args, kwargs):
    integration = langchain_core._datadog_integration
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
async def traced_base_tool_ainvoke(langchain_core, pin, func, instance, args, kwargs):
    integration = langchain_core._datadog_integration
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


@with_traced_module
def patched_base_prompt_template_invoke(langchain_core, pin, func, instance, args, kwargs):
    """
    No actual tracing happens here--we just need to move the prompt template to somewhere it can be accessed later.
    """
    integration: LangChainIntegration = langchain_core._datadog_integration
    if integration.llmobs_enabled is False:
        return func(*args, **kwargs)

    prompt = func(*args, **kwargs)
    integration.handle_prompt_template_invoke(instance, prompt, args, kwargs)
    return prompt


@with_traced_module
async def patched_base_prompt_template_ainvoke(langchain_core, pin, func, instance, args, kwargs):
    """
    async version of above patched_base_prompt_template_invoke
    """
    integration: LangChainIntegration = langchain_core._datadog_integration
    if integration.llmobs_enabled is False:
        return await func(*args, **kwargs)

    prompt = await func(*args, **kwargs)
    integration.handle_prompt_template_invoke(instance, prompt, args, kwargs)
    return prompt


@with_traced_module
def patched_language_model_invoke(langchain_core, pin, func, instance, args, kwargs):
    """
    Wrapper for BaseLLM.invoke() and BaseChatModel.invoke() methods to handle prompt template metadata transfer.

    BaseLLM.invoke() wraps BaseLLM.generate(), converting .invoke()'s input (often a PromptValue
    with templating information) into a string.
    While most tagging happens in the .generate() wrapper, we need to enter here to capture
    that templating information before it is consumed.
    Since child spans may need to read the tagged data, we must tag before calling the wrapped function.
    """
    integration: LangChainIntegration = langchain_core._datadog_integration
    if integration.llmobs_enabled is False:
        return func(*args, **kwargs)

    integration.handle_llm_invoke(instance, args, kwargs)
    response = func(*args, **kwargs)
    return response


@with_traced_module
async def patched_language_model_ainvoke(langchain_core, pin, func, instance, args, kwargs):
    """
    async version of above patched_language_model_invoke
    """
    integration: LangChainIntegration = langchain_core._datadog_integration
    if integration.llmobs_enabled is False:
        return await func(*args, **kwargs)

    integration.handle_llm_invoke(instance, args, kwargs)
    response = await func(*args, **kwargs)
    return response


@with_traced_module
def traced_embedding(langchain_core, pin, func, instance, args, kwargs):
    provider = instance.__class__.__name__.split("Embeddings")[0].lower()
    if provider == "openai" and func.__name__ == "embed_query":
        return func(*args, **kwargs)  # we previously did not trace OpenAIEmbeddings.embed_query

    integration: LangChainIntegration = langchain_core._datadog_integration
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
def traced_similarity_search(langchain_core, pin, func, instance, args, kwargs):
    integration: LangChainIntegration = langchain_core._datadog_integration
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


def patched_embeddings_init_subclass(func, instance, args, kwargs):
    func(*args, **kwargs)
    cls = func.__self__

    try:
        embed_documents = getattr(cls, "embed_documents", None)
        if embed_documents and not isinstance(embed_documents, wrapt.ObjectProxy):
            wrap(cls, "embed_documents", traced_embedding(langchain_core))

        embed_query = getattr(cls, "embed_query", None)
        if embed_query and not isinstance(embed_query, wrapt.ObjectProxy):
            wrap(cls, "embed_query", traced_embedding(langchain_core))
    except Exception:
        log.warning("Unable to patch LangChain Embeddings class %s", str(cls))


def patched_vectorstore_init_subclass(func, instance, args, kwargs):
    func(*args, **kwargs)
    cls = func.__self__

    try:
        method = getattr(cls, "similarity_search", None)
        if method and not isinstance(method, wrapt.ObjectProxy):
            wrap(cls, "similarity_search", traced_similarity_search(langchain_core))
    except Exception:
        log.warning("Unable to patch LangChain VectorStore class %s", str(cls))


def patch():
    if getattr(langchain_core, "_datadog_patch", False):
        return

    langchain_core._datadog_patch = True
    integration = LangChainIntegration(integration_config=config.langchain)
    langchain_core._datadog_integration = integration

    Pin().onto(langchain_core)

    from langchain_core.embeddings import Embeddings
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.language_models.llms import BaseLLM
    from langchain_core.prompts.base import BasePromptTemplate
    from langchain_core.runnables.base import RunnableSequence
    from langchain_core.tools import BaseTool
    from langchain_core.vectorstores import VectorStore

    wrap(BaseLLM, "generate", traced_llm_generate(langchain_core))
    wrap(BaseLLM, "agenerate", traced_llm_agenerate(langchain_core))
    wrap(BaseLLM, "invoke", patched_language_model_invoke(langchain_core))
    wrap(BaseLLM, "ainvoke", patched_language_model_ainvoke(langchain_core))
    wrap(BaseLLM, "stream", traced_llm_stream(langchain_core))
    wrap(BaseLLM, "astream", traced_llm_stream(langchain_core))

    wrap(BaseChatModel, "generate", traced_chat_model_generate(langchain_core))
    wrap(BaseChatModel, "agenerate", traced_chat_model_agenerate(langchain_core))
    wrap(BaseChatModel, "invoke", patched_language_model_invoke(langchain_core))
    wrap(BaseChatModel, "ainvoke", patched_language_model_ainvoke(langchain_core))
    wrap(BaseChatModel, "stream", traced_chat_stream(langchain_core))
    wrap(BaseChatModel, "astream", traced_chat_stream(langchain_core))

    wrap(RunnableSequence, "invoke", traced_lcel_runnable_sequence(langchain_core))
    wrap(RunnableSequence, "ainvoke", traced_lcel_runnable_sequence_async(langchain_core))
    wrap(RunnableSequence, "batch", traced_lcel_runnable_sequence(langchain_core))
    wrap(RunnableSequence, "abatch", traced_lcel_runnable_sequence_async(langchain_core))
    wrap(RunnableSequence, "stream", traced_chain_stream(langchain_core))
    wrap(RunnableSequence, "astream", traced_chain_stream(langchain_core))

    wrap(BasePromptTemplate, "invoke", patched_base_prompt_template_invoke(langchain_core))
    wrap(BasePromptTemplate, "ainvoke", patched_base_prompt_template_ainvoke(langchain_core))

    wrap(BaseTool, "invoke", traced_base_tool_invoke(langchain_core))
    wrap(BaseTool, "ainvoke", traced_base_tool_ainvoke(langchain_core))

    wrap(Embeddings, "__init_subclass__", patched_embeddings_init_subclass)
    wrap(VectorStore, "__init_subclass__", patched_vectorstore_init_subclass)

    core.dispatch("langchain.patch", tuple())


def unpatch():
    if not getattr(langchain_core, "_datadog_patch", False):
        return

    langchain_core._datadog_patch = False

    unwrap(langchain_core.language_models.llms.BaseLLM, "generate")
    unwrap(langchain_core.language_models.llms.BaseLLM, "agenerate")
    unwrap(langchain_core.language_models.llms.BaseLLM, "invoke")
    unwrap(langchain_core.language_models.llms.BaseLLM, "ainvoke")
    unwrap(langchain_core.language_models.chat_models.BaseChatModel, "generate")
    unwrap(langchain_core.language_models.chat_models.BaseChatModel, "agenerate")
    unwrap(langchain_core.language_models.chat_models.BaseChatModel, "invoke")
    unwrap(langchain_core.language_models.chat_models.BaseChatModel, "ainvoke")
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
    unwrap(langchain_core.prompts.base.BasePromptTemplate, "invoke")
    unwrap(langchain_core.prompts.base.BasePromptTemplate, "ainvoke")
    unwrap(langchain_core.embeddings.Embeddings, "__init_subclass__")
    unwrap(langchain_core.vectorstores.VectorStore, "__init_subclass__")
