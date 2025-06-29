import os
import sys
from typing import Any
from typing import Dict
from typing import Optional
from typing import Union

import langchain
from pydantic import SecretStr


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
from ddtrace.contrib.internal.langchain.constants import API_KEY
from ddtrace.contrib.internal.langchain.constants import text_embedding_models
from ddtrace.contrib.internal.langchain.constants import vectorstore_classes
from ddtrace.contrib.internal.langchain.utils import shared_stream
from ddtrace.contrib.internal.langchain.utils import tag_general_message_input
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
        "span_prompt_completion_sample_rate": float(os.getenv("DD_LANGCHAIN_SPAN_PROMPT_COMPLETION_SAMPLE_RATE", 1.0)),
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


def _format_api_key(api_key: Union[str, SecretStr]) -> str:
    """Obfuscate a given LLM provider API key by returning the last four characters."""
    if hasattr(api_key, "get_secret_value"):
        api_key = api_key.get_secret_value()

    if not api_key or len(api_key) < 4:
        return ""
    return "...%s" % api_key[-4:]


def _extract_api_key(instance: Any) -> str:
    """
    Extract and format LLM-provider API key from instance.
    Note that langchain's LLM/ChatModel/Embeddings interfaces do not have a
    standard attribute name for storing the provider-specific API key, so make a
    best effort here by checking for attributes that end with `api_key/api_token`.
    """
    api_key_attrs = [a for a in dir(instance) if a.endswith(("api_token", "api_key"))]
    if api_key_attrs and hasattr(instance, str(api_key_attrs[0])):
        api_key = getattr(instance, api_key_attrs[0], None)
        if api_key:
            return _format_api_key(api_key)
    return ""


def _tag_openai_token_usage(span: Span, llm_output: Dict[str, Any]) -> None:
    """
    Extract token usage from llm_output, tag on span.
    Calculate the total cost for each LLM/chat_model, then propagate those values up the trace so that
    the root span will store the total token_usage/cost of all of its descendants.
    """
    for token_type in ("prompt", "completion", "total"):
        current_metric_value = span.get_metric("langchain.tokens.%s_tokens" % token_type) or 0
        metric_value = llm_output["token_usage"].get("%s_tokens" % token_type, 0)
        span.set_metric("langchain.tokens.%s_tokens" % token_type, current_metric_value + metric_value)


def _is_openai_llm_instance(instance):
    """Safely check if a traced instance is an OpenAI LLM.
    langchain_community does not automatically import submodules which may result in AttributeErrors.
    """
    try:
        if langchain_openai:
            return isinstance(instance, langchain_openai.OpenAI)
        if langchain_community:
            return isinstance(instance, langchain_community.llms.OpenAI)
        return False
    except (AttributeError, ModuleNotFoundError, ImportError):
        return False


def _is_openai_chat_instance(instance):
    """Safely check if a traced instance is an OpenAI Chat Model.
    langchain_community does not automatically import submodules which may result in AttributeErrors.
    """
    try:
        if langchain_openai:
            return isinstance(instance, langchain_openai.ChatOpenAI)
        if langchain_community:
            return isinstance(instance, langchain_community.chat_models.ChatOpenAI)
        return False
    except (AttributeError, ModuleNotFoundError, ImportError):
        return False


def _is_pinecone_vectorstore_instance(instance):
    """Safely check if a traced instance is a Pinecone VectorStore.
    langchain_community does not automatically import submodules which may result in AttributeErrors.
    """
    try:
        if langchain_pinecone:
            return isinstance(instance, langchain_pinecone.PineconeVectorStore)
        if langchain_community:
            return isinstance(instance, langchain_community.vectorstores.Pinecone)
        return False
    except (AttributeError, ModuleNotFoundError, ImportError):
        return False


@with_traced_module
def traced_llm_generate(langchain, pin, func, instance, args, kwargs):
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
        api_key=_extract_api_key(instance),
        instance=instance,
    )
    completions = None

    integration.record_instance(instance, span)

    try:
        if integration.is_pc_sampled_span(span):
            for idx, prompt in enumerate(prompts):
                span.set_tag_str("langchain.request.prompts.%d" % idx, integration.trunc(str(prompt)))
        for param, val in getattr(instance, "_identifying_params", {}).items():
            if isinstance(val, dict):
                for k, v in val.items():
                    span.set_tag_str("langchain.request.%s.parameters.%s.%s" % (llm_provider, param, k), str(v))
            else:
                span.set_tag_str("langchain.request.%s.parameters.%s" % (llm_provider, param), str(val))

        completions = func(*args, **kwargs)

        core.dispatch("langchain.llm.generate.after", (prompts, completions))

        if _is_openai_llm_instance(instance):
            _tag_openai_token_usage(span, completions.llm_output)

        for idx, completion in enumerate(completions.generations):
            if integration.is_pc_sampled_span(span):
                span.set_tag_str("langchain.response.completions.%d.text" % idx, integration.trunc(completion[0].text))
            if completion and completion[0].generation_info is not None:
                span.set_tag_str(
                    "langchain.response.completions.%d.finish_reason" % idx,
                    str(completion[0].generation_info.get("finish_reason")),
                )
                span.set_tag_str(
                    "langchain.response.completions.%d.logprobs" % idx,
                    str(completion[0].generation_info.get("logprobs")),
                )
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
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
        api_key=_extract_api_key(instance),
        instance=instance,
    )

    integration.record_instance(instance, span)

    completions = None
    try:
        if integration.is_pc_sampled_span(span):
            for idx, prompt in enumerate(prompts):
                span.set_tag_str("langchain.request.prompts.%d" % idx, integration.trunc(str(prompt)))
        for param, val in getattr(instance, "_identifying_params", {}).items():
            if isinstance(val, dict):
                for k, v in val.items():
                    span.set_tag_str("langchain.request.%s.parameters.%s.%s" % (llm_provider, param, k), str(v))
            else:
                span.set_tag_str("langchain.request.%s.parameters.%s" % (llm_provider, param), str(val))

        completions = await func(*args, **kwargs)

        core.dispatch("langchain.llm.agenerate.after", (prompts, completions))

        if _is_openai_llm_instance(instance):
            _tag_openai_token_usage(span, completions.llm_output)

        for idx, completion in enumerate(completions.generations):
            if integration.is_pc_sampled_span(span):
                span.set_tag_str("langchain.response.completions.%d.text" % idx, integration.trunc(completion[0].text))
            if completion and completion[0].generation_info is not None:
                span.set_tag_str(
                    "langchain.response.completions.%d.finish_reason" % idx,
                    str(completion[0].generation_info.get("finish_reason")),
                )
                span.set_tag_str(
                    "langchain.response.completions.%d.logprobs" % idx,
                    str(completion[0].generation_info.get("logprobs")),
                )
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
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
        api_key=_extract_api_key(instance),
        instance=instance,
    )

    integration.record_instance(instance, span)

    chat_completions = None
    try:
        for message_set_idx, message_set in enumerate(chat_messages):
            for message_idx, message in enumerate(message_set):
                if integration.is_pc_sampled_span(span):
                    if isinstance(message, dict):
                        span.set_tag_str(
                            "langchain.request.messages.%d.%d.content" % (message_set_idx, message_idx),
                            integration.trunc(str(message.get("content", ""))),
                        )
                    else:
                        span.set_tag_str(
                            "langchain.request.messages.%d.%d.content" % (message_set_idx, message_idx),
                            integration.trunc(str(getattr(message, "content", ""))),
                        )
                span.set_tag_str(
                    "langchain.request.messages.%d.%d.message_type" % (message_set_idx, message_idx),
                    message.__class__.__name__,
                )
        for param, val in getattr(instance, "_identifying_params", {}).items():
            if isinstance(val, dict):
                for k, v in val.items():
                    span.set_tag_str("langchain.request.%s.parameters.%s.%s" % (llm_provider, param, k), str(v))
            else:
                span.set_tag_str("langchain.request.%s.parameters.%s" % (llm_provider, param), str(val))

        chat_completions = func(*args, **kwargs)

        core.dispatch("langchain.chatmodel.generate.after", (chat_messages, chat_completions))

        if _is_openai_chat_instance(instance):
            _tag_openai_token_usage(span, chat_completions.llm_output)

        for message_set_idx, message_set in enumerate(chat_completions.generations):
            for idx, chat_completion in enumerate(message_set):
                if integration.is_pc_sampled_span(span):
                    text = chat_completion.text
                    message = chat_completion.message
                    # tool calls aren't available on this property for legacy chains
                    tool_calls = getattr(message, "tool_calls", None)

                    if text:
                        span.set_tag_str(
                            "langchain.response.completions.%d.%d.content" % (message_set_idx, idx),
                            integration.trunc(chat_completion.text),
                        )
                    if tool_calls:
                        if not isinstance(tool_calls, list):
                            tool_calls = [tool_calls]
                        for tool_call_idx, tool_call in enumerate(tool_calls):
                            span.set_tag_str(
                                "langchain.response.completions.%d.%d.tool_calls.%d.id"
                                % (message_set_idx, idx, tool_call_idx),
                                str(tool_call.get("id", "")),
                            )
                            span.set_tag_str(
                                "langchain.response.completions.%d.%d.tool_calls.%d.name"
                                % (message_set_idx, idx, tool_call_idx),
                                str(tool_call.get("name", "")),
                            )
                            for arg_name, arg_value in tool_call.get("args", {}).items():
                                span.set_tag_str(
                                    "langchain.response.completions.%d.%d.tool_calls.%d.args.%s"
                                    % (message_set_idx, idx, tool_call_idx, arg_name),
                                    integration.trunc(str(arg_value)),
                                )
                span.set_tag_str(
                    "langchain.response.completions.%d.%d.message_type" % (message_set_idx, idx),
                    chat_completion.message.__class__.__name__,
                )
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
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
        api_key=_extract_api_key(instance),
        instance=instance,
    )

    integration.record_instance(instance, span)

    chat_completions = None
    try:
        for message_set_idx, message_set in enumerate(chat_messages):
            for message_idx, message in enumerate(message_set):
                if integration.is_pc_sampled_span(span):
                    if isinstance(message, dict):
                        span.set_tag_str(
                            "langchain.request.messages.%d.%d.content" % (message_set_idx, message_idx),
                            integration.trunc(str(message.get("content", ""))),
                        )
                    else:
                        span.set_tag_str(
                            "langchain.request.messages.%d.%d.content" % (message_set_idx, message_idx),
                            integration.trunc(str(getattr(message, "content", ""))),
                        )
                span.set_tag_str(
                    "langchain.request.messages.%d.%d.message_type" % (message_set_idx, message_idx),
                    message.__class__.__name__,
                )
        for param, val in getattr(instance, "_identifying_params", {}).items():
            if isinstance(val, dict):
                for k, v in val.items():
                    span.set_tag_str("langchain.request.%s.parameters.%s.%s" % (llm_provider, param, k), str(v))
            else:
                span.set_tag_str("langchain.request.%s.parameters.%s" % (llm_provider, param), str(val))

        chat_completions = await func(*args, **kwargs)

        core.dispatch("langchain.chatmodel.agenerate.after", (chat_messages, chat_completions))

        if _is_openai_chat_instance(instance):
            _tag_openai_token_usage(span, chat_completions.llm_output)

        for message_set_idx, message_set in enumerate(chat_completions.generations):
            for idx, chat_completion in enumerate(message_set):
                if integration.is_pc_sampled_span(span):
                    text = chat_completion.text
                    message = chat_completion.message
                    tool_calls = getattr(message, "tool_calls", None)

                    if text:
                        span.set_tag_str(
                            "langchain.response.completions.%d.%d.content" % (message_set_idx, idx),
                            integration.trunc(chat_completion.text),
                        )
                    if tool_calls:
                        if not isinstance(tool_calls, list):
                            tool_calls = [tool_calls]
                        for tool_call_idx, tool_call in enumerate(tool_calls):
                            span.set_tag_str(
                                "langchain.response.completions.%d.%d.tool_calls.%d.id"
                                % (message_set_idx, idx, tool_call_idx),
                                str(tool_call.get("id", "")),
                            )
                            span.set_tag_str(
                                "langchain.response.completions.%d.%d.tool_calls.%d.name"
                                % (message_set_idx, idx, tool_call_idx),
                                str(tool_call.get("name", "")),
                            )
                            for arg_name, arg_value in tool_call.get("args", {}).items():
                                span.set_tag_str(
                                    "langchain.response.completions.%d.%d.tool_calls.%d.args.%s"
                                    % (message_set_idx, idx, tool_call_idx, arg_name),
                                    integration.trunc(str(arg_value)),
                                )
                span.set_tag_str(
                    "langchain.response.completions.%d.%d.message_type" % (message_set_idx, idx),
                    chat_completion.message.__class__.__name__,
                )
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=chat_completions, operation="chat")
        span.finish()
    return chat_completions


@with_traced_module
def traced_embedding(langchain, pin, func, instance, args, kwargs):
    """
    This traces both embed_query(text) and embed_documents(texts), so we need to make sure
    we get the right arg/kwarg.
    """
    try:
        input_texts = get_argument_value(args, kwargs, 0, "texts")
    except ArgumentError:
        input_texts = get_argument_value(args, kwargs, 0, "text")

    provider = instance.__class__.__name__.split("Embeddings")[0].lower()
    integration = langchain._datadog_integration
    span = integration.trace(
        pin,
        "%s.%s" % (instance.__module__, instance.__class__.__name__),
        submit_to_llmobs=True,
        interface_type="embedding",
        provider=provider,
        model=_extract_model_name(instance),
        api_key=_extract_api_key(instance),
        instance=instance,
    )

    integration.record_instance(instance, span)

    embeddings = None
    try:
        if isinstance(input_texts, str):
            if integration.is_pc_sampled_span(span):
                span.set_tag_str("langchain.request.inputs.0.text", integration.trunc(input_texts))
            span.set_metric("langchain.request.input_count", 1)
        else:
            if integration.is_pc_sampled_span(span):
                for idx, text in enumerate(input_texts):
                    span.set_tag_str("langchain.request.inputs.%d.text" % idx, integration.trunc(text))
            span.set_metric("langchain.request.input_count", len(input_texts))
        # langchain currently does not support token tracking for OpenAI embeddings:
        #  https://github.com/hwchase17/langchain/issues/945
        embeddings = func(*args, **kwargs)
        if isinstance(embeddings, list) and embeddings and isinstance(embeddings[0], list):
            for idx, embedding in enumerate(embeddings):
                span.set_metric("langchain.response.outputs.%d.embedding_length" % idx, len(embedding))
        else:
            span.set_metric("langchain.response.outputs.embedding_length", len(embeddings))
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
        if integration.is_pc_sampled_span(span):
            if not isinstance(inputs, list):
                inputs = [inputs]
            for idx, inp in enumerate(inputs):
                if not isinstance(inp, dict):
                    span.set_tag_str("langchain.request.inputs.%d" % idx, integration.trunc(str(inp)))
                else:
                    for k, v in inp.items():
                        span.set_tag_str("langchain.request.inputs.%d.%s" % (idx, k), integration.trunc(str(v)))
        final_output = func(*args, **kwargs)
        if integration.is_pc_sampled_span(span):
            final_outputs = final_output  # separate variable as to return correct value later
            if not isinstance(final_outputs, list):
                final_outputs = [final_outputs]
            for idx, output in enumerate(final_outputs):
                span.set_tag_str("langchain.response.outputs.%d" % idx, integration.trunc(str(output)))
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
        if integration.is_pc_sampled_span(span):
            if not isinstance(inputs, list):
                inputs = [inputs]
            for idx, inp in enumerate(inputs):
                if not isinstance(inp, dict):
                    span.set_tag_str("langchain.request.inputs.%d" % idx, integration.trunc(str(inp)))
                else:
                    for k, v in inp.items():
                        span.set_tag_str("langchain.request.inputs.%d.%s" % (idx, k), integration.trunc(str(v)))
        final_output = await func(*args, **kwargs)
        if integration.is_pc_sampled_span(span):
            final_outputs = final_output  # separate variable as to return correct value later
            if not isinstance(final_outputs, list):
                final_outputs = [final_outputs]
            for idx, output in enumerate(final_outputs):
                span.set_tag_str("langchain.response.outputs.%d" % idx, integration.trunc(str(output)))
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
    query = get_argument_value(args, kwargs, 0, "query")
    k = kwargs.get("k", args[1] if len(args) >= 2 else None)
    provider = instance.__class__.__name__.lower()
    span = integration.trace(
        pin,
        "%s.%s" % (instance.__module__, instance.__class__.__name__),
        submit_to_llmobs=True,
        interface_type="similarity_search",
        provider=provider,
        api_key=_extract_api_key(instance),
        instance=instance,
    )

    integration.record_instance(instance, span)

    documents = []
    try:
        if integration.is_pc_sampled_span(span):
            span.set_tag_str("langchain.request.query", integration.trunc(query))
        if k is not None:
            span.set_tag_str("langchain.request.k", str(k))
        for kwarg_key, v in kwargs.items():
            span.set_tag_str("langchain.request.%s" % kwarg_key, str(v))
        if _is_pinecone_vectorstore_instance(instance) and hasattr(instance._index, "configuration"):
            span.set_tag_str(
                "langchain.request.pinecone.environment",
                instance._index.configuration.server_variables.get("environment", ""),
            )
            span.set_tag_str(
                "langchain.request.pinecone.index_name",
                instance._index.configuration.server_variables.get("index_name", ""),
            )
            span.set_tag_str(
                "langchain.request.pinecone.project_name",
                instance._index.configuration.server_variables.get("project_name", ""),
            )
            api_key = instance._index.configuration.api_key.get("ApiKeyAuth", "")
            span.set_tag_str(API_KEY, _format_api_key(api_key))  # override api_key for Pinecone
        documents = func(*args, **kwargs)
        span.set_metric("langchain.response.document_count", len(documents))
        for idx, document in enumerate(documents):
            span.set_tag_str(
                "langchain.response.document.%d.page_content" % idx, integration.trunc(str(document.page_content))
            )
            for kwarg_key, v in document.metadata.items():
                span.set_tag_str(
                    "langchain.response.document.%d.metadata.%s" % (idx, kwarg_key), integration.trunc(str(v))
                )
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
        inputs = get_argument_value(args, kwargs, 0, "input")
        if not integration.is_pc_sampled_span(span):
            return
        if not isinstance(inputs, list):
            inputs = [inputs]
        for idx, inp in enumerate(inputs):
            if not isinstance(inp, dict):
                span.set_tag_str("langchain.request.inputs.%d" % idx, integration.trunc(str(inp)))
                continue
            for k, v in inp.items():
                span.set_tag_str("langchain.request.inputs.%d.%s" % (idx, k), integration.trunc(str(v)))

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
        if span.error or not integration.is_pc_sampled_span(span):
            return
        span.set_tag_str("langchain.response.outputs", integration.trunc(content))

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
        if not integration.is_pc_sampled_span(span):
            return
        chat_messages = get_argument_value(args, kwargs, 0, "input")
        tag_general_message_input(span, chat_messages, integration, langchain_core)

        for param, val in getattr(instance, "_identifying_params", {}).items():
            if not isinstance(val, dict):
                span.set_tag_str("langchain.request.%s.parameters.%s" % (llm_provider, param), str(val))
                continue
            for k, v in val.items():
                span.set_tag_str("langchain.request.%s.parameters.%s.%s" % (llm_provider, param, k), str(v))

    def _on_span_finished(span: Span, streamed_chunks):
        joined_chunks = streamed_chunks[0]
        for chunk in streamed_chunks[1:]:
            joined_chunks += chunk  # base message types support __add__ for concatenation
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=joined_chunks, operation="chat")
        if (
            span.error
            or not integration.is_pc_sampled_span(span)
            or streamed_chunks is None
            or len(streamed_chunks) == 0
        ):
            return
        content = str(getattr(joined_chunks, "content", joined_chunks))
        role = joined_chunks.__class__.__name__.replace("Chunk", "")  # AIMessageChunk --> AIMessage
        span.set_tag_str("langchain.response.content", integration.trunc(content))
        if role:
            span.set_tag_str("langchain.response.message_type", role)

        usage = streamed_chunks and getattr(streamed_chunks[-1], "usage_metadata", None)
        if not usage or not isinstance(usage, dict):
            return
        for k, v in usage.items():
            span.set_tag_str("langchain.response.usage_metadata.%s" % k, str(v))

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
        api_key=_extract_api_key(instance),
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
        if not integration.is_pc_sampled_span(span):
            return
        inp = get_argument_value(args, kwargs, 0, "input")
        tag_general_message_input(span, inp, integration, langchain_core)
        for param, val in getattr(instance, "_identifying_params", {}).items():
            if not isinstance(val, dict):
                span.set_tag_str("langchain.request.%s.parameters.%s" % (llm_provider, param), str(val))
                continue
            for k, v in val.items():
                span.set_tag_str("langchain.request.%s.parameters.%s.%s" % (llm_provider, param, k), str(v))

    def _on_span_finished(span: Span, streamed_chunks):
        content = "".join([str(chunk) for chunk in streamed_chunks])
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=content, operation="llm")
        if span.error or not integration.is_pc_sampled_span(span):
            return
        span.set_tag_str("langchain.response.content", integration.trunc(content))

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
        api_key=_extract_api_key(instance),
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
            if value is not None:
                span.set_tag_str("langchain.request.tool.%s" % attribute, str(value))

        metadata = getattr(instance, "metadata", {})
        if metadata:
            tool_info["metadata"] = metadata
            for key, meta_value in metadata.items():
                span.set_tag_str("langchain.request.tool.metadata.%s" % key, str(meta_value))
        tags = getattr(instance, "tags", [])
        if tags:
            tool_info["tags"] = tags
            for idx, tag in tags:
                span.set_tag_str("langchain.request.tool.tags.%d" % idx, str(value))

        if tool_input and integration.is_pc_sampled_span(span):
            span.set_tag_str("langchain.request.input", integration.trunc(str(tool_input)))
        if config:
            span.set_tag_str("langchain.request.config", safe_json(config))

        tool_output = func(*args, **kwargs)
        if tool_output and integration.is_pc_sampled_span(span):
            span.set_tag_str("langchain.response.output", integration.trunc(str(tool_output)))
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
            if value is not None:
                span.set_tag_str("langchain.request.tool.%s" % attribute, str(value))

        metadata = getattr(instance, "metadata", {})
        if metadata:
            tool_info["metadata"] = metadata
            for key, meta_value in metadata.items():
                span.set_tag_str("langchain.request.tool.metadata.%s" % key, str(meta_value))
        tags = getattr(instance, "tags", [])
        if tags:
            tool_info["tags"] = tags
            for idx, tag in tags:
                span.set_tag_str("langchain.request.tool.tags.%d" % idx, str(value))

        if tool_input and integration.is_pc_sampled_span(span):
            span.set_tag_str("langchain.request.input", integration.trunc(str(tool_input)))
        if config:
            span.set_tag_str("langchain.request.config", safe_json(config))

        tool_output = await func(*args, **kwargs)
        if tool_output and integration.is_pc_sampled_span(span):
            span.set_tag_str("langchain.response.output", integration.trunc(str(tool_output)))
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
