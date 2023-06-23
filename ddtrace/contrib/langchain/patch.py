import os
from typing import TYPE_CHECKING

import langchain
from langchain.callbacks import get_openai_callback

from ddtrace import config
from ddtrace.contrib.langchain.constants import text_embedding_models
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.contrib.trace_utils import wrap
from ddtrace.contrib.trace_utils_llm import BaseLLMIntegration
from ddtrace.internal.agent import get_stats_url
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import asbool
from ddtrace.pin import Pin


if TYPE_CHECKING:
    from ddtrace import Span


log = get_logger(__name__)


config._add(
    "langchain",
    {
        "logs_enabled": asbool(os.getenv("DD_LANGCHAIN_LOGS_ENABLED", False)),
        "metrics_enabled": asbool(os.getenv("DD_LANGCHAIN_METRICS_ENABLED", True)),
        "span_prompt_completion_sample_rate": float(os.getenv("DD_LANGCHAIN_SPAN_PROMPT_COMPLETION_SAMPLE_RATE", 1.0)),
        "log_prompt_completion_sample_rate": float(os.getenv("DD_LANGCHAIN_LOG_PROMPT_COMPLETION_SAMPLE_RATE", 0.1)),
        "span_char_limit": int(os.getenv("DD_LANGCHAIN_SPAN_CHAR_LIMIT", 128)),
        "_api_key": os.getenv("DD_API_KEY"),
    },
)


class _LangChainIntegration(BaseLLMIntegration):
    _integration_name = "langchain"

    def __init__(self, config, stats_url, site, api_key):
        super().__init__(config, stats_url, site, api_key)

    def _set_base_span_tags(self, span):
        # type: (Span) -> None
        return None

    def _logs_tags(self, span):
        # type: (Span) -> str
        tags = "env:%s,version:%s,langchain.request.provider:%s,langchain.request.model:%s" % (  # noqa: E501
            (config.env or ""),
            (config.version or ""),
            (span.get_tag("langchain.request.provider") or ""),
            (span.get_tag("langchain.request.model") or ""),
        )
        return tags

    def _metrics_tags(self, span):
        tags = [
            "version:%s" % (config.version or ""),
            "env:%s" % (config.env or ""),
            "service:%s" % (span.service or ""),
            "langchain.request.provider:%s" % (span.get_tag("langchain.request.provider") or ""),
            "langchain.request.model:%s" % (span.get_tag("langchain.request.model") or ""),
            "error:%d" % span.error,
        ]
        err_type = span.get_tag("error.type")
        if err_type:
            tags.append("error_type:%s" % err_type)
        return tags


# TODO: set up rate sampler for prompt-completion tagging?
# TODO: truncate/remove whitespace on prompt/completions?
# TODO: Logging?
# TODO: tag api key?
# TODO: how should we name the spans? Langchain-provided Chains/LLMs vs custom user chain/LLM
# Currently naming the span `langchain.request`, with resource name `{module}.{class-name}`


def _extract_model_name(instance):
    """Extract model name or ID from llm instance."""
    for attr in ("model", "model_name", "model_id", "model_key"):
        if hasattr(instance, attr):
            return getattr(instance, attr, None)
    return None


def _tag_token_usage(span, callback):
    """Extract token usage from callback, tag on span."""
    # TODO: Langchain's `get_openai_callback()` only works on one top-level call at a time, i.e.
    #  it only tracks token usage at the top-level chain, then returns 0 for all nested chains/llms.
    #  the current solution (below) is to only call it once per llm and then propagate those values up
    #  the trace so that the root chain will store the total token usage of all of its children.
    if span._parent is not None:
        _tag_token_usage(span._parent, callback)
    for metric in ("total_cost", "total_tokens", "prompt_tokens", "completion_tokens"):
        metric_value = span.get_metric("langchain.tokens.%s" % metric) or 0
        span.set_metric("langchain.tokens.%s" % metric, metric_value + getattr(callback, metric, 0))


@with_traced_module
def traced_llm_generate(langchain, pin, func, instance, args, kwargs):
    llm_provider = instance._llm_type
    prompts = args[0]
    integration = langchain._datadog_integration
    with integration.trace(pin, "%s.%s" % (instance.__module__, instance.__class__.__name__)) as span:
        if integration.is_pc_sampled_span(span):
            for idx, prompt in enumerate(prompts):
                span.set_tag_str("langchain.request.prompts.%d" % idx, integration.trunc(prompt))
        model = _extract_model_name(instance)
        if model is not None:
            span.set_tag_str("langchain.request.model", model)
        span.set_tag_str("langchain.request.provider", llm_provider)
        for param, val in getattr(instance, "_default_params", {}).items():
            span.set_tag_str("langchain.request.%s.parameters.%s" % (llm_provider, param), str(val))

        if isinstance(instance, langchain.llms.OpenAI):
            with get_openai_callback() as cb:
                completions = func(*args, **kwargs)
            _tag_token_usage(span, cb)
        else:
            completions = func(*args, **kwargs)

        for idx, completion in enumerate(completions.generations):
            if integration.is_pc_sampled_span(span):
                span.set_tag_str("langchain.response.completions.%d.text" % idx, integration.trunc(completion[0].text))
            span.set_tag_str(
                "langchain.response.completions.%d.finish_reason" % idx,
                completion[0].generation_info.get("finish_reason"),
            )
            span.set_tag_str(
                "langchain.response.completions.%d.logprobs" % idx,
                str(completion[0].generation_info.get("logprobs")),
            )

        if integration.is_pc_sampled_log(span):
            integration.log(
                span,
                "info" if span.error == 0 else "error",
                "sampled %s.%s" % (instance.__module__, instance.__class__.__name__),
                attrs={
                    "prompt": prompts,
                    "choices": [
                        [
                            {
                                "text": completion.text,
                                "finish_reason": completion.generation_info.get("finish_reason"),
                                "logprobs": completion.generation_info.get("logprobs"),
                            }
                            for completion in completions
                        ]
                        for completions in completions.generations
                    ],
                },
            )
    return completions


@with_traced_module
async def traced_llm_agenerate(langchain, pin, func, instance, args, kwargs):
    llm_provider = instance._llm_type
    prompts = args[0]
    integration = langchain._datadog_integration
    with integration.trace(pin, "%s.%s" % (instance.__module__, instance.__class__.__name__)) as span:
        if integration.is_pc_sampled_span(span):
            for idx, prompt in enumerate(prompts):
                span.set_tag_str("langchain.request.prompts.%d" % idx, integration.trunc(prompt))
        model = _extract_model_name(instance)
        if model is not None:
            span.set_tag_str("langchain.request.model", model)
        span.set_tag_str("langchain.request.provider", llm_provider)
        for param, val in getattr(instance, "_default_params", {}).items():
            span.set_tag_str("langchain.request.%s.parameters.%s" % (llm_provider, param), str(val))
        if isinstance(instance, langchain.llms.OpenAI):
            with get_openai_callback() as cb:
                completions = await func(*args, **kwargs)
            _tag_token_usage(span, cb)
        else:
            completions = await func(*args, **kwargs)

        for idx, completion in enumerate(completions.generations):
            if integration.is_pc_sampled_span(span):
                span.set_tag_str("langchain.response.completions.%d.text" % idx, integration.trunc(completion[0].text))
            span.set_tag_str(
                "langchain.response.completions.%d.finish_reason" % idx,
                completion[0].generation_info.get("finish_reason"),
            )
            span.set_tag_str(
                "langchain.response.completions.%d.logprobs" % idx,
                str(completion[0].generation_info.get("logprobs")),
            )
        if integration.is_pc_sampled_log(span):
            integration.log(
                span,
                "info" if span.error == 0 else "error",
                "sampled %s.%s" % (instance.__module__, instance.__class__.__name__),
                attrs={
                    "prompt": prompts,
                    "choices": [
                        [
                            {
                                "text": completion.text,
                                "finish_reason": completion.generation_info.get("finish_reason"),
                                "logprobs": completion.generation_info.get("logprobs"),
                            }
                            for completion in completions
                        ]
                        for completions in completions.generations
                    ],
                },
            )
    return completions


@with_traced_module
def traced_chat_model_generate(langchain, pin, func, instance, args, kwargs):
    llm_provider = instance._llm_type.split("-")[0]
    chat_messages = args[0]
    integration = langchain._datadog_integration
    with integration.trace(pin, "%s.%s" % (instance.__module__, instance.__class__.__name__)) as span:
        for message_set_idx, message_set in enumerate(chat_messages):
            for message_idx, message in enumerate(message_set):
                if integration.is_pc_sampled_span(span):
                    span.set_tag_str(
                        "langchain.request.messages.%d.%d.content" % (message_set_idx, message_idx),
                        integration.trunc(message.content),
                    )
                span.set_tag_str(
                    "langchain.request.messages.%d.%d.message_type" % (message_set_idx, message_idx),
                    message.__class__.__name__,
                )
        model = _extract_model_name(instance)
        if model is not None:
            span.set_tag_str("langchain.request.model", model)
        span.set_tag_str("langchain.request.provider", llm_provider)
        for param, val in getattr(instance, "_default_params", {}).items():
            span.set_tag_str("langchain.request.%s.parameters.%s" % (llm_provider, param), str(val))
        if isinstance(instance, langchain.chat_models.ChatOpenAI):
            with get_openai_callback() as cb:
                chat_completions = func(*args, **kwargs)
            _tag_token_usage(span, cb)
        else:
            chat_completions = func(*args, **kwargs)
        for message_set_idx, message_set in enumerate(chat_completions.generations):
            for idx, chat_completion in enumerate(message_set):
                if integration.is_pc_sampled_span(span):
                    span.set_tag_str(
                        "langchain.response.completions.%d.%d.content" % (message_set_idx, idx),
                        integration.trunc(chat_completion.text),
                    )
                span.set_tag_str(
                    "langchain.response.completions.%d.%d.message_type" % (message_set_idx, idx),
                    chat_completion.message.__class__.__name__,
                )

        if integration.is_pc_sampled_log(span):
            integration.log(
                span,
                "info" if span.error == 0 else "error",
                "sampled %s.%s" % (instance.__module__, instance.__class__.__name__),
                attrs={
                    "messages": [
                        [
                            {
                                "content": message.content,
                                "message_type": message.__class__.__name__,
                            }
                            for message in messages
                        ]
                        for messages in chat_messages
                    ],
                    "choices": [
                        [
                            {
                                "content": message.text,
                                "message_type": message.message.__class__.__name__,
                            }
                            for message in messages
                        ]
                        for messages in chat_completions.generations
                    ],
                },
            )

    return chat_completions


@with_traced_module
async def traced_chat_model_agenerate(langchain, pin, func, instance, args, kwargs):
    llm_provider = instance._llm_type.split("-")[0]
    chat_messages = args[0]
    integration = langchain._datadog_integration
    with integration.trace(pin, "%s.%s" % (instance.__module__, instance.__class__.__name__)) as span:
        for message_set_idx, message_set in enumerate(chat_messages):
            for message_idx, message in enumerate(message_set):
                if integration.is_pc_sampled_span(span):
                    span.set_tag_str(
                        "langchain.request.messages.%d.%d.content" % (message_set_idx, message_idx),
                        integration.trunc(message.content),
                    )
                span.set_tag_str(
                    "langchain.request.messages.%d.%d.message_type" % (message_set_idx, message_idx),
                    message.__class__.__name__,
                )
        model = _extract_model_name(instance)
        if model is not None:
            span.set_tag_str("langchain.request.model", model)
        span.set_tag_str("langchain.request.provider", llm_provider)
        for param, val in getattr(instance, "_default_params", {}).items():
            span.set_tag_str("langchain.request.%s.parameters.%s" % (llm_provider, param), str(val))
        if isinstance(instance, langchain.chat_models.ChatOpenAI):
            with get_openai_callback() as cb:
                chat_completions = await func(*args, **kwargs)
            _tag_token_usage(span, cb)
        else:
            chat_completions = await func(*args, **kwargs)
        for message_set_idx, message_set in enumerate(chat_completions.generations):
            for idx, chat_completion in enumerate(message_set):
                if integration.is_pc_sampled_span(span):
                    span.set_tag_str(
                        "langchain.response.completions.%d.%d.content" % (message_set_idx, idx),
                        integration.trunc(chat_completion.text),
                    )
                span.set_tag_str(
                    "langchain.response.completions.%d.%d.message_type" % (message_set_idx, idx),
                    chat_completion.message.__class__.__name__,
                )

        if integration.is_pc_sampled_log(span):
            integration.log(
                span,
                "info" if span.error == 0 else "error",
                "sampled %s.%s" % (instance.__module__, instance.__class__.__name__),
                attrs={
                    "messages": [
                        [
                            {
                                "content": message.content,
                                "message_type": message.__class__.__name__,
                            }
                            for message in messages
                        ]
                        for messages in chat_messages
                    ],
                    "choices": [
                        [
                            {
                                "content": message.text,
                                "message_type": message.message.__class__.__name__,
                            }
                            for message in messages
                        ]
                        for messages in chat_completions.generations
                    ],
                },
            )

    return chat_completions


@with_traced_module
def traced_embedding(langchain, pin, func, instance, args, kwargs):
    input_texts = args[0]
    provider = instance.__class__.__name__.split("Embeddings")[0].lower()
    integration = langchain._datadog_integration
    with integration.trace(pin, "%s.%s" % (instance.__module__, instance.__class__.__name__)) as span:
        if isinstance(input_texts, str):
            if integration.is_pc_sampled_span(span):
                span.set_tag_str("langchain.request.inputs.0.text", integration.trunc(input_texts))
            span.set_metric("langchain.request.input_count", 1)
        else:
            if integration.is_pc_sampled_span(span):
                for idx, text in enumerate(input_texts):
                    span.set_tag_str("langchain.request.inputs.%d.text" % idx, integration.trunc(text))
            span.set_metric("langchain.request.input_count", len(input_texts))
        model = _extract_model_name(instance)
        if model is not None:
            span.set_tag_str("langchain.request.model", model)
        span.set_tag_str("langchain.request.provider", provider)
        # langchain currently does not support token tracking for OpenAI embeddings:
        #  https://github.com/hwchase17/langchain/issues/945
        embeddings = func(*args, **kwargs)
        if isinstance(embeddings, list) and isinstance(embeddings[0], list):
            for idx, embedding in enumerate(embeddings):
                span.set_metric("langchain.response.outputs.%d.embedding_length" % idx, len(embedding))
        else:
            span.set_metric("langchain.response.outputs.embedding_length", len(embeddings))

        if integration.is_pc_sampled_log(span):
            integration.log(
                span,
                "info" if span.error == 0 else "error",
                "sampled %s.%s" % (instance.__module__, instance.__class__.__name__),
                attrs={"inputs": input_texts},
            )

        return embeddings


@with_traced_module
def traced_chain_call(langchain, pin, func, instance, args, kwargs):
    integration = langchain._datadog_integration
    with integration.trace(pin, "%s.%s" % (instance.__module__, instance.__class__.__name__)) as span:
        inputs = args[0]
        if isinstance(inputs, dict):
            for k, v in inputs.items():
                span.set_tag_str("langchain.request.inputs.%s" % k, integration.trunc(str(v)))
        else:
            span.set_tag_str("langchain.request.inputs.%s" % instance.input_keys[0], integration.trunc(str(inputs)))
        if hasattr(instance, "prompt") and integration.is_pc_sampled_span(span):
            span.set_tag_str("langchain.request.prompt", integration.trunc(str(instance.prompt.template)))
        final_outputs = func(*args, **kwargs)
        if integration.is_pc_sampled_span(span):
            for k, v in final_outputs.items():
                span.set_tag_str("langchain.response.outputs.%s" % k, integration.trunc(str(v)))

        if integration.is_pc_sampled_log(span):
            integration.log(
                span,
                "info" if span.error == 0 else "error",
                "sampled %s.%s" % (instance.__module__, instance.__class__.__name__),
                attrs={
                    "inputs": inputs,
                    "prompt": str(instance.prompt.template) or "",
                    "outputs": final_outputs,
                },
            )

    return final_outputs


@with_traced_module
async def traced_chain_acall(langchain, pin, func, instance, args, kwargs):
    integration = langchain._datadog_integration
    with integration.trace(pin, "%s.%s" % (instance.__module__, instance.__class__.__name__)) as span:
        inputs = args[0]
        if isinstance(inputs, dict):
            for k, v in inputs.items():
                span.set_tag_str("langchain.request.inputs.%s" % k, str(v))
        else:
            span.set_tag_str("langchain.request.inputs.%s" % instance.input_keys[0], inputs)
        if hasattr(instance, "prompt") and integration.is_pc_sampled_span(span):
            span.set_tag_str("langchain.request.prompt", integration.trunc(str(instance.prompt.template)))
        final_outputs = await func(*args, **kwargs)
        if integration.is_pc_sampled_span(span):
            for k, v in final_outputs.items():
                span.set_tag_str("langchain.response.outputs.%s" % k, integration.trunc(str(v)))

        if integration.is_pc_sampled_log(span):
            integration.log(
                span,
                "info" if span.error == 0 else "error",
                "sampled %s.%s" % (instance.__module__, instance.__class__.__name__),
                attrs={
                    "inputs": inputs,
                    "prompt": str(instance.prompt.template) or "",
                    "outputs": final_outputs,
                },
            )

    return final_outputs


@with_traced_module
def traced_similarity_search(langchain, pin, func, instance, args, kwargs):
    return func(*args, **kwargs)


def patch():
    if getattr(langchain, "_datadog_patch", False):
        return
    setattr(langchain, "_datadog_patch", True)

    #  TODO: How do we test this? Can we mock out the metric/logger/sampler?
    ddsite = os.getenv("DD_SITE", "datadoghq.com")
    ddapikey = os.getenv("DD_API_KEY", config.langchain._api_key)

    Pin().onto(langchain)
    integration = _LangChainIntegration(
        config=config.langchain,
        stats_url=get_stats_url(),
        site=ddsite,
        api_key=ddapikey,
    )
    setattr(langchain, "_datadog_integration", integration)

    if config.langchain.logs_enabled:
        if not ddapikey:
            raise ValueError("DD_API_KEY is required for sending logs from the LangChain integration")

    # TODO: check if we need to version gate LLM/Chat/TextEmbedding
    wrap("langchain", "llms.base.BaseLLM.generate", traced_llm_generate(langchain))
    wrap("langchain", "llms.BaseLLM.agenerate", traced_llm_agenerate(langchain))
    wrap("langchain", "chat_models.base.BaseChatModel.generate", traced_chat_model_generate(langchain))
    wrap("langchain", "chat_models.base.BaseChatModel.agenerate", traced_chat_model_agenerate(langchain))
    # Text embedding models override two abstract base methods instead of super calls, so we need to
    #  wrap each langchain-provided text embedding model.
    for text_embedding_model in text_embedding_models:
        wrap("langchain", "embeddings.%s.embed_query" % text_embedding_model, traced_embedding(langchain))
        wrap("langchain", "embeddings.%s.embed_documents" % text_embedding_model, traced_embedding(langchain))
    wrap("langchain", "chains.base.Chain.__call__", traced_chain_call(langchain))
    wrap("langchain", "chains.base.Chain.acall", traced_chain_acall(langchain))

    # wrap("langchain", "vectorstores.Pinecone.similarity_search", traced_similarity_search(langchain))


def unpatch():
    if getattr(langchain, "_datadog_patch", False):
        setattr(langchain, "_datadog_patch", False)

    unwrap(langchain.llms.base.BaseLLM, "generate")
    unwrap(langchain.llms.base.BaseLLM, "agenerate")
    unwrap(langchain.chat_models.base.BaseChatModel, "generate")
    unwrap(langchain.chat_models.base.BaseChatModel, "agenerate")
    for text_embedding_model in text_embedding_models:
        unwrap(getattr(langchain.embeddings, text_embedding_model), "embed_query")
        unwrap(getattr(langchain.embeddings, text_embedding_model), "embed_documents")
    unwrap(langchain.chains.base.Chain, "__call__")
    unwrap(langchain.chains.base.Chain, "acall")
