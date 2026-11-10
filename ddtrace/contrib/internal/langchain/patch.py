from collections.abc import Callable
from collections.abc import Sequence
from types import MethodType
from typing import Any
from typing import Optional

import langchain_core

from ddtrace import config
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.contrib.internal.bedrock_inference_profiles import record_inference_profile
from ddtrace.contrib.internal.langchain.utils import shared_stream
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.internal import core
from ddtrace.internal import integration_hooks
from ddtrace.internal.compat import is_wrapted
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import ArgumentError
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import safe_json


log = get_logger(__name__)


def get_version() -> str:
    return getattr(langchain_core, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"langchain_core": ">=0.1"}


config._add("langchain", {})

# LLMObs subscribes to shared lifecycle events for this component; see ddtrace/llmobs/_contrib/langchain.
COMPONENT = "langchain"

# Keep APM model/provider tags available even without LLMObs subscriber hooks.
MODEL_TAG = "langchain.request.model"
PROVIDER_TAG = "langchain.request.provider"
STREAM_TAG = "langchain.request.stream"


def _extract_model_name(instance: Any) -> Optional[str]:
    """Extract model name or ID from llm instance.

    Strips path prefixes (e.g. "models/gemini-2.5-flash" → "gemini-2.5-flash")
    since some providers use resource paths rather than bare model names.

    `base_model_id` is checked first so that langchain-aws ChatBedrockConverse
    instances using an inference profile (where `model_id` is the profile ARN
    and `base_model_id` is the underlying foundation model) report the
    foundation model rather than the opaque profile identifier.

    When the instance carries both an application-inference-profile
    ARN in `model_id` and a `base_model_id`, the mapping is stored in a shared
    process-local cache so the botocore Bedrock integration can resolve the
    same ARN on its own span without an extra AWS call.
    """
    model_id_attr = getattr(instance, "model_id", None)
    base_model_id_attr = getattr(instance, "base_model_id", None)
    if (
        isinstance(model_id_attr, str)
        and isinstance(base_model_id_attr, str)
        and "application-inference-profile/" in model_id_attr
    ):
        record_inference_profile(model_id_attr, base_model_id_attr)

    for attr in ("base_model_id", "model", "model_name", "model_id", "model_key", "repo_id"):
        model_name = getattr(instance, attr, None)
        if not model_name:
            continue
        if isinstance(model_name, str) and "/" in model_name:
            model_name = model_name.split("/")[-1]
        return model_name
    return None


_GENERIC_LLM_TYPE_PARTS = {"chat", "llm"}


def _extract_model_provider(instance: Any) -> str:
    """Extract the model provider from a LangChain model instance.

    LangChain's _llm_type mixes interface type with provider name inconsistently:
      - "openai-chat" (provider first)
      - "chat-google-generativeai" (interface first)
    Filter out generic interface terms to isolate the actual provider.
    """
    parts = instance._llm_type.split("-")
    provider_parts = [p for p in parts if p not in _GENERIC_LLM_TYPE_PARTS]
    return "-".join(provider_parts) if provider_parts else instance._llm_type


def _request_event(
    resource: str,
    instance: Any,
    operation: str,
    args: Sequence[Any] = (),
    kwargs: Optional[dict[str, Any]] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    stream: bool = False,
) -> LlmRequestEvent:
    """Build the LlmRequestEvent for a traced LangChain call.

    args and kwargs are what LangChainIntegration extracts LLMObs data from at span finish;
    the chain and tool wrappers pass their own normalized inputs as kwargs.
    """
    tags = {}
    if provider is not None:
        tags[PROVIDER_TAG] = provider
    if model is not None:
        tags[MODEL_TAG] = model
    if stream:
        tags[STREAM_TAG] = "True"
    return LlmRequestEvent(
        component=COMPONENT,
        integration_config=config.langchain,
        service=int_service(None, config.langchain),
        resource=resource,
        provider=provider,
        model=model,
        tags=tags,
        submit_to_llmobs=True,
        request_args=tuple(args),
        request_kwargs=kwargs if kwargs is not None else {},
        instance=instance,
        operation=operation,
    )


def _instance_resource(instance: Any) -> str:
    return "%s.%s" % (instance.__module__, instance.__class__.__name__)


def traced_llm_generate(func, instance, args, kwargs):
    prompts = get_argument_value(args, kwargs, 0, "prompts")
    event = _request_event(
        _instance_resource(instance),
        instance,
        "llm",
        args=args,
        kwargs=kwargs,
        provider=instance._llm_type,
        model=_extract_model_name(instance),
    )
    with core.context_with_event(event):
        try:
            core.dispatch("langchain.llm.generate.before", (prompts,), allow_raise=True)
            completions = func(*args, **kwargs)
            event.response = completions
            # allow_raise so a listener's block decision on the response propagates to the
            # caller instead of the response being returned
            core.dispatch("langchain.llm.generate.after", (prompts, completions), allow_raise=True)
            return completions
        finally:
            core.dispatch("langchain.llm.generate.finally", ())
            kwargs["_dd.identifying_params"] = instance._identifying_params


async def traced_llm_agenerate(func, instance, args, kwargs):
    prompts = get_argument_value(args, kwargs, 0, "prompts")
    event = _request_event(
        _instance_resource(instance),
        instance,
        "llm",
        args=args,
        kwargs=kwargs,
        provider=instance._llm_type,
        model=_extract_model_name(instance),
    )
    with core.context_with_event(event):
        try:
            core.dispatch("langchain.llm.agenerate.before", (prompts,), allow_raise=True)
            completions = await func(*args, **kwargs)
            event.response = completions
            # allow_raise so a listener's block decision on the response propagates to the
            # caller instead of the response being returned
            core.dispatch("langchain.llm.agenerate.after", (prompts, completions), allow_raise=True)
            return completions
        finally:
            core.dispatch("langchain.llm.agenerate.finally", ())
            kwargs["_dd.identifying_params"] = instance._identifying_params


def traced_chat_model_generate(func, instance, args, kwargs):
    chat_messages = get_argument_value(args, kwargs, 0, "messages")
    event = _request_event(
        _instance_resource(instance),
        instance,
        "chat",
        args=args,
        kwargs=kwargs,
        provider=_extract_model_provider(instance),
        model=_extract_model_name(instance),
    )
    with core.context_with_event(event):
        try:
            core.dispatch("langchain.chatmodel.generate.before", (chat_messages,), allow_raise=True)
            chat_completions = func(*args, **kwargs)
            event.response = chat_completions
            # allow_raise so a listener's block decision on the response propagates to the
            # caller instead of the response being returned
            core.dispatch("langchain.chatmodel.generate.after", (chat_messages, chat_completions), allow_raise=True)
            return chat_completions
        finally:
            core.dispatch("langchain.chatmodel.generate.finally", ())
            kwargs["_dd.identifying_params"] = instance._identifying_params


async def traced_chat_model_agenerate(func, instance, args, kwargs):
    chat_messages = get_argument_value(args, kwargs, 0, "messages")
    event = _request_event(
        _instance_resource(instance),
        instance,
        "chat",
        args=args,
        kwargs=kwargs,
        provider=_extract_model_provider(instance),
        model=_extract_model_name(instance),
    )
    with core.context_with_event(event):
        try:
            core.dispatch("langchain.chatmodel.agenerate.before", (chat_messages,), allow_raise=True)
            chat_completions = await func(*args, **kwargs)
            event.response = chat_completions
            # allow_raise so a listener's block decision on the response propagates to the
            # caller instead of the response being returned
            core.dispatch("langchain.chatmodel.agenerate.after", (chat_messages, chat_completions), allow_raise=True)
            return chat_completions
        finally:
            core.dispatch("langchain.chatmodel.agenerate.finally", ())
            kwargs["_dd.identifying_params"] = instance._identifying_params


def _chain_inputs(args: tuple[Any, ...], kwargs: dict[str, Any]) -> list[Any]:
    try:
        inputs = get_argument_value(args, kwargs, 0, "input")
    except ArgumentError:
        inputs = get_argument_value(args, kwargs, 0, "inputs")
    return inputs if isinstance(inputs, list) else [inputs]


def traced_lcel_runnable_sequence(func, instance, args, kwargs):
    """
    Traces the top level call of a LangChain Expression Language (LCEL) chain.

    LCEL is a new way of chaining in LangChain. It works by piping the output of one step of a chain into the next.
    This is similar in concept to the legacy LLMChain class, but instead relies internally on the idea of a
    RunnableSequence. It uses the operator `|` to create an implicit chain of `Runnable` steps.

    It works with a set of useful tools that distill legacy ways of creating chains,
    and various tasks and tooling within, making it preferable to LLMChain and related classes.

    This method captures the initial inputs to the chain, as well as the final outputs, and tags them appropriately.
    """
    event = _request_event(_instance_resource(instance), instance, "chain")
    with core.context_with_event(event):
        # LangChainIntegration reads non-streamed chain inputs from the kwargs slot.
        event.request_kwargs = _chain_inputs(args, kwargs)
        final_output = func(*args, **kwargs)
        event.response = final_output
        return final_output


async def traced_lcel_runnable_sequence_async(func, instance, args, kwargs):
    """
    Similar to `traced_lcel_runnable_sequence`, but for async chaining calls.
    """
    event = _request_event(_instance_resource(instance), instance, "chain")
    with core.context_with_event(event):
        event.request_kwargs = _chain_inputs(args, kwargs)
        final_output = await func(*args, **kwargs)
        event.response = final_output
        return final_output


def traced_chain_stream(func, instance, args, kwargs):
    def _on_stream_finished(streamed_chunks: list[Any]) -> Any:
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
                return safe_json(result)
            return str(result)
        # best effort to join chunks together
        return "".join([str(chunk) for chunk in streamed_chunks])

    event = _request_event(_instance_resource(instance), instance, "chain", args=args, kwargs=kwargs, stream=True)
    return shared_stream(
        event=event,
        func=func,
        instance=instance,
        args=args,
        kwargs=kwargs,
        interface_type="chain",
        on_stream_finished=_on_stream_finished,
    )


def traced_chat_stream(func, instance, args, kwargs):
    def _on_stream_finished(streamed_chunks: list[Any]) -> Any:
        kwargs["_dd.identifying_params"] = instance._identifying_params
        if not streamed_chunks:
            return []
        joined_chunks = streamed_chunks[0]
        for chunk in streamed_chunks[1:]:
            joined_chunks += chunk  # base message types support __add__ for concatenation
        return joined_chunks

    event = _request_event(
        _instance_resource(instance),
        instance,
        "chat",
        args=args,
        kwargs=kwargs,
        provider=instance._llm_type,
        model=_extract_model_name(instance),
        stream=True,
    )
    return shared_stream(
        event=event,
        func=func,
        instance=instance,
        args=args,
        kwargs=kwargs,
        interface_type="chat_model",
        on_stream_finished=_on_stream_finished,
        aiguard_before_event="langchain.chatmodel.stream.before",
        aiguard_started_event="langchain.chatmodel.stream.started",
        aiguard_finally_event="langchain.chatmodel.stream.finally",
    )


def traced_llm_stream(func, instance, args, kwargs):
    def _on_stream_finished(streamed_chunks: list[Any]) -> str:
        kwargs["_dd.identifying_params"] = instance._identifying_params
        return "".join([str(chunk) for chunk in streamed_chunks])

    event = _request_event(
        _instance_resource(instance),
        instance,
        "llm",
        args=args,
        kwargs=kwargs,
        provider=instance._llm_type,
        model=_extract_model_name(instance),
        stream=True,
    )
    return shared_stream(
        event=event,
        func=func,
        instance=instance,
        args=args,
        kwargs=kwargs,
        interface_type="llm",
        on_stream_finished=_on_stream_finished,
        aiguard_before_event="langchain.llm.stream.before",
        aiguard_started_event="langchain.llm.stream.started",
        aiguard_finally_event="langchain.llm.stream.finally",
    )


def _tool_request_event(
    func: MethodType, instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> LlmRequestEvent:
    tool_input = get_argument_value(args, kwargs, 0, "input")
    tool_config = get_argument_value(args, kwargs, 1, "config", optional=True)
    tool_info = {}
    for attribute in ("name", "description"):
        tool_info[attribute] = getattr(instance, attribute, None)
    metadata = getattr(instance, "metadata", {})
    if metadata:
        tool_info["metadata"] = metadata
    tags = getattr(instance, "tags", [])
    if tags:
        tool_info["tags"] = tags
    # LangChainIntegration reads tool inputs from the kwargs slot.
    tool_inputs = {"input": tool_input, "config": tool_config or {}, "info": tool_info}
    tool: Any = func.__self__
    return _request_event("%s" % tool.name, instance, "tool", kwargs=tool_inputs)


def traced_base_tool_invoke(func, instance, args, kwargs):
    event = _tool_request_event(func, instance, args, kwargs)
    with core.context_with_event(event):
        tool_output = func(*args, **kwargs)
        event.response = tool_output
        return tool_output


async def traced_base_tool_ainvoke(func, instance, args, kwargs):
    event = _tool_request_event(func, instance, args, kwargs)
    with core.context_with_event(event):
        tool_output = await func(*args, **kwargs)
        event.response = tool_output
        return tool_output


def patched_base_prompt_template_invoke(func, instance, args, kwargs):
    """
    No actual tracing happens here--we just need to move the prompt template to somewhere it can be accessed later.
    """
    prompt = func(*args, **kwargs)
    core.dispatch("langchain.prompt_template.invoke.after", (instance, prompt, args, kwargs))
    return prompt


async def patched_base_prompt_template_ainvoke(func, instance, args, kwargs):
    """
    async version of above patched_base_prompt_template_invoke
    """
    prompt = await func(*args, **kwargs)
    core.dispatch("langchain.prompt_template.invoke.after", (instance, prompt, args, kwargs))
    return prompt


def patched_language_model_invoke(func, instance, args, kwargs):
    """
    Wrapper for BaseLLM.invoke() and BaseChatModel.invoke() methods to handle prompt template metadata transfer.

    BaseLLM.invoke() wraps BaseLLM.generate(), converting .invoke()'s input (often a PromptValue
    with templating information) into a string.
    While most tagging happens in the .generate() wrapper, we need to enter here to capture
    that templating information before it is consumed.
    Since child spans may need to read the tagged data, we must tag before calling the wrapped function.
    """
    core.dispatch("langchain.language_model.invoke.before", (instance, args, kwargs))
    return func(*args, **kwargs)


async def patched_language_model_ainvoke(func, instance, args, kwargs):
    """
    async version of above patched_language_model_invoke
    """
    core.dispatch("langchain.language_model.invoke.before", (instance, args, kwargs))
    return await func(*args, **kwargs)


def traced_embedding(func, instance, args, kwargs):
    provider = instance.__class__.__name__.split("Embeddings")[0].lower()
    if provider == "openai" and func.__name__ == "embed_query":
        return func(*args, **kwargs)  # we previously did not trace OpenAIEmbeddings.embed_query

    event = _request_event(
        _instance_resource(instance),
        instance,
        "embedding",
        args=args,
        kwargs=kwargs,
        provider=provider,
        model=_extract_model_name(instance),
    )
    with core.context_with_event(event):
        embeddings = func(*args, **kwargs)
        event.response = embeddings
        return embeddings


def traced_similarity_search(func, instance, args, kwargs):
    provider = instance.__class__.__name__.lower()
    event = _request_event(
        _instance_resource(instance), instance, "retrieval", args=args, kwargs=kwargs, provider=provider
    )
    with core.context_with_event(event):
        documents = func(*args, **kwargs)
        event.response = documents
        return documents


def patched_embeddings_init_subclass(func, instance, args, kwargs):
    func(*args, **kwargs)
    cls = func.__self__

    try:
        embed_documents = getattr(cls, "embed_documents", None)
        if embed_documents and not is_wrapted(embed_documents):
            wrap(cls, "embed_documents", traced_embedding)

        embed_query = getattr(cls, "embed_query", None)
        if embed_query and not is_wrapted(embed_query):
            wrap(cls, "embed_query", traced_embedding)
    except Exception:
        log.warning("Unable to patch LangChain Embeddings class %s", str(cls))


def patched_vectorstore_init_subclass(func, instance, args, kwargs):
    func(*args, **kwargs)
    cls = func.__self__

    try:
        method = getattr(cls, "similarity_search", None)
        if method and not is_wrapted(method):
            wrap(cls, "similarity_search", traced_similarity_search)
    except Exception:
        log.warning("Unable to patch LangChain VectorStore class %s", str(cls))


def _unwrap_init_subclass(cls: type) -> None:
    """Remove the __init_subclass__ wrapper from cls without pinning the original to cls.

    The generic unwrap() stores the original bound to cls in cls.__dict__, so after a later
    patch() the hooks above would receive cls as func.__self__ instead of each new subclass,
    leaving subclasses defined after an unpatch/patch cycle untraced.
    """
    wrapper = cls.__dict__.get("__init_subclass__")
    if wrapper is None or not is_wrapted(wrapper):
        return
    original = wrapper.__wrapped__
    if any(base.__dict__.get("__init_subclass__") is original for base in cls.__mro__[1:]):
        delattr(cls, "__init_subclass__")
    else:
        setattr(cls, "__init_subclass__", original)


def _runnable_lambda_span_name(func: Callable[..., Any], instance: Any, is_batch: bool) -> str:
    instance_name = getattr(instance, "name", None)
    default_name = f"{instance.__class__.__name__}.{func.__name__}"
    if is_batch:
        return f"{instance_name}_batch" if instance_name else default_name
    return instance_name or default_name


def traced_runnable_lambda_operation(is_batch: bool = False):
    def _traced_runnable_lambda_impl(func, instance, args, kwargs):
        event = _request_event(
            _runnable_lambda_span_name(func, instance, is_batch),
            instance,
            "runnable_lambda",
            args=args,
            kwargs=kwargs,
        )
        with core.context_with_event(event):
            result = func(*args, **kwargs)
            event.response = result
            return result

    return _traced_runnable_lambda_impl


def traced_runnable_lambda_operation_async(is_batch: bool = False):
    async def _traced_runnable_lambda_impl(func, instance, args, kwargs):
        event = _request_event(
            _runnable_lambda_span_name(func, instance, is_batch),
            instance,
            "runnable_lambda",
            args=args,
            kwargs=kwargs,
        )
        with core.context_with_event(event):
            result = await func(*args, **kwargs)
            event.response = result
            return result

    return _traced_runnable_lambda_impl


def patch():
    if getattr(langchain_core, "_datadog_patch", False):
        return

    langchain_core._datadog_patch = True

    from langchain_core.embeddings import Embeddings
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.language_models.llms import BaseLLM
    from langchain_core.prompts.base import BasePromptTemplate
    from langchain_core.runnables.base import RunnableLambda
    from langchain_core.runnables.base import RunnableSequence
    from langchain_core.tools import BaseTool
    from langchain_core.vectorstores import VectorStore

    wrap(BaseLLM, "generate", traced_llm_generate)
    wrap(BaseLLM, "agenerate", traced_llm_agenerate)
    wrap(BaseLLM, "invoke", patched_language_model_invoke)
    wrap(BaseLLM, "ainvoke", patched_language_model_ainvoke)
    wrap(BaseLLM, "stream", traced_llm_stream)
    wrap(BaseLLM, "astream", traced_llm_stream)

    wrap(BaseChatModel, "generate", traced_chat_model_generate)
    wrap(BaseChatModel, "agenerate", traced_chat_model_agenerate)
    wrap(BaseChatModel, "invoke", patched_language_model_invoke)
    wrap(BaseChatModel, "ainvoke", patched_language_model_ainvoke)
    wrap(BaseChatModel, "stream", traced_chat_stream)
    wrap(BaseChatModel, "astream", traced_chat_stream)

    wrap(RunnableSequence, "invoke", traced_lcel_runnable_sequence)
    wrap(RunnableSequence, "ainvoke", traced_lcel_runnable_sequence_async)
    wrap(RunnableSequence, "batch", traced_lcel_runnable_sequence)
    wrap(RunnableSequence, "abatch", traced_lcel_runnable_sequence_async)
    wrap(RunnableSequence, "stream", traced_chain_stream)
    wrap(RunnableSequence, "astream", traced_chain_stream)

    wrap(RunnableLambda, "invoke", traced_runnable_lambda_operation(is_batch=False))
    wrap(RunnableLambda, "ainvoke", traced_runnable_lambda_operation_async(is_batch=False))
    wrap(RunnableLambda, "batch", traced_runnable_lambda_operation(is_batch=True))
    wrap(RunnableLambda, "abatch", traced_runnable_lambda_operation_async(is_batch=True))

    wrap(BasePromptTemplate, "invoke", patched_base_prompt_template_invoke)
    wrap(BasePromptTemplate, "ainvoke", patched_base_prompt_template_ainvoke)

    wrap(BaseTool, "invoke", traced_base_tool_invoke)
    wrap(BaseTool, "ainvoke", traced_base_tool_ainvoke)

    wrap(Embeddings, "__init_subclass__", patched_embeddings_init_subclass)
    wrap(VectorStore, "__init_subclass__", patched_vectorstore_init_subclass)

    core.dispatch("langchain.patch", tuple())

    # Let products (LLMObs) attach their shared lifecycle subscribers without this
    # module importing them.
    integration_hooks.on_patch(COMPONENT)


def unpatch():
    if not getattr(langchain_core, "_datadog_patch", False):
        return

    langchain_core._datadog_patch = False

    # Listeners wrapped after this integration did, so they unwrap first and each layer comes off in order.
    core.dispatch("langchain.unpatch", tuple())

    integration_hooks.on_unpatch(COMPONENT)

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
    unwrap(langchain_core.runnables.base.RunnableLambda, "invoke")
    unwrap(langchain_core.runnables.base.RunnableLambda, "ainvoke")
    unwrap(langchain_core.runnables.base.RunnableLambda, "batch")
    unwrap(langchain_core.runnables.base.RunnableLambda, "abatch")
    unwrap(langchain_core.language_models.chat_models.BaseChatModel, "stream")
    unwrap(langchain_core.language_models.chat_models.BaseChatModel, "astream")
    unwrap(langchain_core.language_models.llms.BaseLLM, "stream")
    unwrap(langchain_core.language_models.llms.BaseLLM, "astream")
    unwrap(langchain_core.tools.BaseTool, "invoke")
    unwrap(langchain_core.tools.BaseTool, "ainvoke")
    unwrap(langchain_core.prompts.base.BasePromptTemplate, "invoke")
    unwrap(langchain_core.prompts.base.BasePromptTemplate, "ainvoke")
    _unwrap_init_subclass(langchain_core.embeddings.Embeddings)
    _unwrap_init_subclass(langchain_core.vectorstores.VectorStore)
