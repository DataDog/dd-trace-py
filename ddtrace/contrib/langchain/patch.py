import os
import sys
from typing import Any
from typing import Dict
from typing import Optional
from typing import TYPE_CHECKING

import langchain
from langchain.callbacks.openai_info import get_openai_token_cost_for_model

from ddtrace import config
from ddtrace.constants import ERROR_TYPE
from ddtrace.contrib._trace_utils_llm import BaseLLMIntegration
from ddtrace.contrib.langchain.constants import API_KEY
from ddtrace.contrib.langchain.constants import COMPLETION_TOKENS
from ddtrace.contrib.langchain.constants import MODEL
from ddtrace.contrib.langchain.constants import PROMPT_TOKENS
from ddtrace.contrib.langchain.constants import PROVIDER
from ddtrace.contrib.langchain.constants import TOTAL_COST
from ddtrace.contrib.langchain.constants import TYPE
from ddtrace.contrib.langchain.constants import text_embedding_models
from ddtrace.contrib.langchain.constants import vectorstores
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.agent import get_stats_url
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import ArgumentError
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.formats import deep_getattr
from ddtrace.pin import Pin
from ddtrace.vendor import wrapt


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

    def _set_base_span_tags(self, span, interface_type="", provider=None, model=None, api_key=None):
        # type: (Span, str, Optional[str], Optional[str], Optional[str]) -> None
        """Set base level tags that should be present on all LangChain spans (if they are not None)."""
        span.set_tag_str(TYPE, interface_type)
        if provider is not None:
            span.set_tag_str(PROVIDER, provider)
        if model is not None:
            span.set_tag_str(MODEL, model)
        if api_key is not None:
            if len(api_key) >= 4:
                span.set_tag_str(API_KEY, "...%s" % str(api_key[-4:]))
            else:
                span.set_tag_str(API_KEY, api_key)

    @classmethod
    def _logs_tags(cls, span):
        # type: (Span) -> str
        api_key = span.get_tag(API_KEY) or ""
        tags = "env:%s,version:%s,%s:%s,%s:%s,%s:%s,%s:%s" % (  # noqa: E501
            (config.env or ""),
            (config.version or ""),
            PROVIDER,
            (span.get_tag(PROVIDER) or ""),
            MODEL,
            (span.get_tag(MODEL) or ""),
            TYPE,
            (span.get_tag(TYPE) or ""),
            API_KEY,
            api_key,
        )
        return tags

    @classmethod
    def _metrics_tags(cls, span):
        # type: (Span) -> list
        provider = span.get_tag(PROVIDER) or ""
        api_key = span.get_tag(API_KEY) or ""
        tags = [
            "version:%s" % (config.version or ""),
            "env:%s" % (config.env or ""),
            "service:%s" % (span.service or ""),
            "%s:%s" % (PROVIDER, provider),
            "%s:%s" % (MODEL, span.get_tag(MODEL) or ""),
            "%s:%s" % (TYPE, span.get_tag(TYPE) or ""),
            "%s:%s" % (API_KEY, api_key),
            "error:%d" % span.error,
        ]
        err_type = span.get_tag(ERROR_TYPE)
        if err_type:
            tags.append("%s:%s" % (ERROR_TYPE, err_type))
        return tags

    def record_usage(self, span, usage):
        # type: (Span, Dict[str, Any]) -> None
        if not usage or self._config.metrics_enabled is False:
            return
        for token_type in ("prompt", "completion", "total"):
            num_tokens = usage.get("token_usage", {}).get(token_type + "_tokens")
            if not num_tokens:
                continue
            self.metric(span, "dist", "tokens.%s" % token_type, num_tokens)
        total_cost = span.get_metric(TOTAL_COST)
        if total_cost:
            self.metric(span, "incr", "tokens.total_cost", total_cost)


def _extract_model_name(instance):
    # type: (langchain.llm.BaseLLM) -> Optional[str]
    """Extract model name or ID from llm instance."""
    for attr in ("model", "model_name", "model_id", "model_key", "repo_id"):
        if hasattr(instance, attr):
            return getattr(instance, attr)
    return None


def _format_api_key(api_key):
    # type: (str) -> str
    """Obfuscate a given LLM provider API key by returning the last four characters."""
    if not api_key or len(api_key) < 4:
        return ""
    return "...%s" % api_key[-4:]


def _extract_api_key(instance):
    # type: (Any) -> str
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


def _tag_openai_token_usage(span, llm_output, propagated_cost=0, propagate=False):
    # type: (Span, Dict[str, Any], int, bool) -> None
    """
    Extract token usage from llm_output, tag on span.
    Calculate the total cost for each LLM/chat_model, then propagate those values up the trace so that
    the root span will store the total token_usage/cost of all of its descendants.
    """
    for token_type in ("prompt", "completion", "total"):
        current_metric_value = span.get_metric("langchain.tokens.%s_tokens" % token_type) or 0
        metric_value = llm_output["token_usage"].get("%s_tokens" % token_type, 0)
        span.set_metric("langchain.tokens.%s_tokens" % token_type, current_metric_value + metric_value)
    total_cost = span.get_metric(TOTAL_COST) or 0
    if not propagate:
        try:
            completion_cost = get_openai_token_cost_for_model(
                span.get_tag(MODEL),
                span.get_metric(COMPLETION_TOKENS),
                is_completion=True,
            )
            prompt_cost = get_openai_token_cost_for_model(span.get_tag(MODEL), span.get_metric(PROMPT_TOKENS))
            total_cost = completion_cost + prompt_cost
        except ValueError:
            # If not in langchain's openai model catalog, the above helpers will raise a ValueError.
            log.debug("Cannot calculate token/cost as the model is not in LangChain's OpenAI model catalog.")
    span.set_metric(TOTAL_COST, propagated_cost + total_cost)
    if span._parent is not None:
        _tag_openai_token_usage(span._parent, llm_output, propagated_cost=propagated_cost + total_cost, propagate=True)


@with_traced_module
def traced_llm_generate(langchain, pin, func, instance, args, kwargs):
    llm_provider = instance._llm_type
    prompts = get_argument_value(args, kwargs, 0, "prompts")
    integration = langchain._datadog_integration
    model = _extract_model_name(instance)
    span = integration.trace(
        pin,
        "%s.%s" % (instance.__module__, instance.__class__.__name__),
        interface_type="llm",
        provider=llm_provider,
        model=model,
        api_key=_extract_api_key(instance),
    )
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

        completions = func(*args, **kwargs)
        if isinstance(instance, langchain.llms.OpenAI):
            _tag_openai_token_usage(span, completions.llm_output)
            integration.record_usage(span, completions.llm_output)

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
        integration.metric(span, "incr", "request.error", 1)
        raise
    finally:
        span.finish()
        integration.metric(span, "dist", "request.duration", span.duration_ns)
        if integration.is_pc_sampled_log(span):
            if completions is None:
                log_completions = []
            else:
                log_completions = [
                    [{"text": completion.text} for completion in completions] for completions in completions.generations
                ]
            integration.log(
                span,
                "info" if span.error == 0 else "error",
                "sampled %s.%s" % (instance.__module__, instance.__class__.__name__),
                attrs={
                    "prompts": prompts,
                    "choices": log_completions,
                },
            )
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
        interface_type="llm",
        provider=llm_provider,
        model=model,
        api_key=_extract_api_key(instance),
    )
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
        if isinstance(instance, langchain.llms.OpenAI):
            _tag_openai_token_usage(span, completions.llm_output)
            integration.record_usage(span, completions.llm_output)

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
        integration.metric(span, "incr", "request.error", 1)
        raise
    finally:
        span.finish()
        integration.metric(span, "dist", "request.duration", span.duration_ns)
        if integration.is_pc_sampled_log(span):
            if completions is None:
                log_completions = []
            else:
                log_completions = [
                    [{"text": completion.text} for completion in completions] for completions in completions.generations
                ]
            integration.log(
                span,
                "info" if span.error == 0 else "error",
                "sampled %s.%s" % (instance.__module__, instance.__class__.__name__),
                attrs={
                    "prompts": prompts,
                    "choices": log_completions,
                },
            )
    return completions


@with_traced_module
def traced_chat_model_generate(langchain, pin, func, instance, args, kwargs):
    llm_provider = instance._llm_type.split("-")[0]
    chat_messages = get_argument_value(args, kwargs, 0, "messages")
    integration = langchain._datadog_integration
    span = integration.trace(
        pin,
        "%s.%s" % (instance.__module__, instance.__class__.__name__),
        interface_type="chat_model",
        provider=llm_provider,
        model=_extract_model_name(instance),
        api_key=_extract_api_key(instance),
    )
    chat_completions = None
    try:
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
        for param, val in getattr(instance, "_identifying_params", {}).items():
            if isinstance(val, dict):
                for k, v in val.items():
                    span.set_tag_str("langchain.request.%s.parameters.%s.%s" % (llm_provider, param, k), str(v))
            else:
                span.set_tag_str("langchain.request.%s.parameters.%s" % (llm_provider, param), str(val))

        chat_completions = func(*args, **kwargs)
        if isinstance(instance, langchain.chat_models.ChatOpenAI):
            _tag_openai_token_usage(span, chat_completions.llm_output)
            integration.record_usage(span, chat_completions.llm_output)

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
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.metric(span, "incr", "request.error", 1)
        raise
    finally:
        span.finish()
        integration.metric(span, "dist", "request.duration", span.duration_ns)
        if integration.is_pc_sampled_log(span):
            if chat_completions is None:
                log_chat_completions = []
            else:
                log_chat_completions = [
                    [
                        {
                            "content": message.text,
                            "message_type": message.message.__class__.__name__,
                        }
                        for message in messages
                    ]
                    for messages in chat_completions.generations
                ]
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
                    "choices": log_chat_completions,
                },
            )
    return chat_completions


@with_traced_module
async def traced_chat_model_agenerate(langchain, pin, func, instance, args, kwargs):
    llm_provider = instance._llm_type.split("-")[0]
    chat_messages = get_argument_value(args, kwargs, 0, "messages")
    integration = langchain._datadog_integration
    span = integration.trace(
        pin,
        "%s.%s" % (instance.__module__, instance.__class__.__name__),
        interface_type="chat_model",
        provider=llm_provider,
        model=_extract_model_name(instance),
        api_key=_extract_api_key(instance),
    )
    chat_completions = None
    try:
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
        for param, val in getattr(instance, "_identifying_params", {}).items():
            if isinstance(val, dict):
                for k, v in val.items():
                    span.set_tag_str("langchain.request.%s.parameters.%s.%s" % (llm_provider, param, k), str(v))
            else:
                span.set_tag_str("langchain.request.%s.parameters.%s" % (llm_provider, param), str(val))

        chat_completions = await func(*args, **kwargs)
        if isinstance(instance, langchain.chat_models.ChatOpenAI):
            _tag_openai_token_usage(span, chat_completions.llm_output)
            integration.record_usage(span, chat_completions.llm_output)

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
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.metric(span, "incr", "request.error", 1)
        raise
    finally:
        span.finish()
        integration.metric(span, "dist", "request.duration", span.duration_ns)
        if integration.is_pc_sampled_log(span):
            if chat_completions is None:
                log_chat_completions = []
            else:
                log_chat_completions = [
                    [
                        {
                            "content": message.text,
                            "message_type": message.message.__class__.__name__,
                        }
                        for message in messages
                    ]
                    for messages in chat_completions.generations
                ]
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
                    "choices": log_chat_completions,
                },
            )
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
        interface_type="embedding",
        provider=provider,
        model=_extract_model_name(instance),
        api_key=_extract_api_key(instance),
    )
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
        if isinstance(embeddings, list) and isinstance(embeddings[0], list):
            for idx, embedding in enumerate(embeddings):
                span.set_metric("langchain.response.outputs.%d.embedding_length" % idx, len(embedding))
        else:
            span.set_metric("langchain.response.outputs.embedding_length", len(embeddings))
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.metric(span, "incr", "request.error", 1)
        raise
    finally:
        span.finish()
        integration.metric(span, "dist", "request.duration", span.duration_ns)
        if integration.is_pc_sampled_log(span):
            integration.log(
                span,
                "info" if span.error == 0 else "error",
                "sampled %s.%s" % (instance.__module__, instance.__class__.__name__),
                attrs={"inputs": [input_texts] if isinstance(input_texts, str) else input_texts},
            )
    return embeddings


@with_traced_module
def traced_chain_call(langchain, pin, func, instance, args, kwargs):
    integration = langchain._datadog_integration
    span = integration.trace(pin, "%s.%s" % (instance.__module__, instance.__class__.__name__), interface_type="chain")
    final_outputs = {}
    try:
        inputs = get_argument_value(args, kwargs, 0, "inputs")
        if not isinstance(inputs, dict):
            inputs = {instance.input_keys[0]: inputs}
        if integration.is_pc_sampled_span(span):
            for k, v in inputs.items():
                span.set_tag_str("langchain.request.inputs.%s" % k, integration.trunc(str(v)))
            template = deep_getattr(instance, "prompt.template", default="")
            if template:
                span.set_tag_str("langchain.request.prompt", integration.trunc(str(template)))
        final_outputs = func(*args, **kwargs)
        if integration.is_pc_sampled_span(span):
            for k, v in final_outputs.items():
                span.set_tag_str("langchain.response.outputs.%s" % k, integration.trunc(str(v)))
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.metric(span, "incr", "request.error", 1)
        raise
    finally:
        span.finish()
        integration.metric(span, "dist", "request.duration", span.duration_ns)
        if integration.is_pc_sampled_log(span):
            log_inputs = {}
            log_outputs = {}
            for k, v in inputs.items():
                log_inputs[k] = str(v)
            for k, v in final_outputs.items():
                log_outputs[k] = str(v)
            integration.log(
                span,
                "info" if span.error == 0 else "error",
                "sampled %s.%s" % (instance.__module__, instance.__class__.__name__),
                attrs={
                    "inputs": log_inputs,
                    "prompt": str(deep_getattr(instance, "prompt.template", default="")),
                    "outputs": log_outputs,
                },
            )
    return final_outputs


@with_traced_module
async def traced_chain_acall(langchain, pin, func, instance, args, kwargs):
    integration = langchain._datadog_integration
    span = integration.trace(pin, "%s.%s" % (instance.__module__, instance.__class__.__name__), interface_type="chain")
    final_outputs = {}
    try:
        inputs = get_argument_value(args, kwargs, 0, "inputs")
        if not isinstance(inputs, dict):
            inputs = {instance.input_keys[0]: inputs}
        if integration.is_pc_sampled_span(span):
            for k, v in inputs.items():
                span.set_tag_str("langchain.request.inputs.%s" % k, integration.trunc(str(v)))
            template = deep_getattr(instance, "prompt.template", default="")
            if template:
                span.set_tag_str("langchain.request.prompt", integration.trunc(str(template)))
        final_outputs = await func(*args, **kwargs)
        if integration.is_pc_sampled_span(span):
            for k, v in final_outputs.items():
                span.set_tag_str("langchain.response.outputs.%s" % k, integration.trunc(str(v)))
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.metric(span, "incr", "request.error", 1)
        raise
    finally:
        span.finish()
        integration.metric(span, "dist", "request.duration", span.duration_ns)
        if integration.is_pc_sampled_log(span):
            log_inputs = {}
            log_outputs = {}
            for k, v in inputs.items():
                log_inputs[k] = str(v)
            for k, v in final_outputs.items():
                log_outputs[k] = str(v)
            integration.log(
                span,
                "info" if span.error == 0 else "error",
                "sampled %s.%s" % (instance.__module__, instance.__class__.__name__),
                attrs={
                    "inputs": log_inputs,
                    "prompt": str(deep_getattr(instance, "prompt.template", default="")),
                    "outputs": log_outputs,
                },
            )
    return final_outputs


@with_traced_module
def traced_similarity_search(langchain, pin, func, instance, args, kwargs):
    integration = langchain._datadog_integration
    query = get_argument_value(args, kwargs, 0, "query")
    k = kwargs.get("k", args[1] if len(args) >= 2 else None)
    provider = instance.__class__.__name__.lower()
    span = integration.trace(
        pin,
        "%s.%s" % (instance.__module__, instance.__class__.__name__),
        interface_type="similarity_search",
        provider=provider,
        api_key=_extract_api_key(instance),
    )
    documents = []
    try:
        if integration.is_pc_sampled_span(span):
            span.set_tag_str("langchain.request.query", integration.trunc(query))
        if k is not None:
            span.set_tag_str("langchain.request.k", str(k))
        for kwarg_key, v in kwargs.items():
            span.set_tag_str("langchain.request.%s" % kwarg_key, str(v))
        if isinstance(instance, langchain.vectorstores.Pinecone):
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
            span.set_tag_str(API_KEY, "...%s" % api_key[-4:])  # override api_key for Pinecone
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
        integration.metric(span, "incr", "request.error", 1)
        raise
    finally:
        span.finish()
        integration.metric(span, "dist", "request.duration", span.duration_ns)
        if integration.is_pc_sampled_log(span):
            integration.log(
                span,
                "info" if span.error == 0 else "error",
                "sampled %s.%s" % (instance.__module__, instance.__class__.__name__),
                attrs={
                    "query": query,
                    "k": k or "",
                    "documents": [
                        {"page_content": document.page_content, "metadata": document.metadata} for document in documents
                    ],
                },
            )
    return documents


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
            raise ValueError(
                "DD_API_KEY is required for sending logs from the LangChain integration."
                " The LangChain integration can be disabled by setting the ``DD_TRACE_LANGCHAIN_ENABLED``"
                " environment variable to False."
            )
        integration.start_log_writer()

    wrap("langchain", "llms.base.BaseLLM.generate", traced_llm_generate(langchain))
    wrap("langchain", "llms.base.BaseLLM.agenerate", traced_llm_agenerate(langchain))
    wrap("langchain", "chat_models.base.BaseChatModel.generate", traced_chat_model_generate(langchain))
    wrap("langchain", "chat_models.base.BaseChatModel.agenerate", traced_chat_model_agenerate(langchain))
    wrap("langchain", "chains.base.Chain.__call__", traced_chain_call(langchain))
    wrap("langchain", "chains.base.Chain.acall", traced_chain_acall(langchain))
    # Text embedding models override two abstract base methods instead of super calls, so we need to
    #  wrap each langchain-provided text embedding model.
    for text_embedding_model in text_embedding_models:
        if hasattr(langchain.embeddings, text_embedding_model):
            # Ensure not double patched, as some Embeddings interfaces are pointers to other Embeddings.
            if not isinstance(
                deep_getattr(langchain.embeddings, "%s.embed_query" % text_embedding_model), wrapt.ObjectProxy
            ):
                wrap("langchain", "embeddings.%s.embed_query" % text_embedding_model, traced_embedding(langchain))
            if not isinstance(
                deep_getattr(langchain.embeddings, "%s.embed_documents" % text_embedding_model), wrapt.ObjectProxy
            ):
                wrap("langchain", "embeddings.%s.embed_documents" % text_embedding_model, traced_embedding(langchain))
                # TODO: langchain >= 0.0.209 includes async embedding implementation (only for OpenAI)
    # We need to do the same with Vectorstores.
    for vectorstore in vectorstores:
        if hasattr(langchain.vectorstores, vectorstore):
            # Ensure not double patched, as some Embeddings interfaces are pointers to other Embeddings.
            if not isinstance(
                deep_getattr(langchain.vectorstores, "%s.similarity_search" % vectorstore), wrapt.ObjectProxy
            ):
                wrap(
                    "langchain", "vectorstores.%s.similarity_search" % vectorstore, traced_similarity_search(langchain)
                )


def unpatch():
    if not getattr(langchain, "_datadog_patch", False):
        return
    setattr(langchain, "_datadog_patch", False)

    unwrap(langchain.llms.base.BaseLLM, "generate")
    unwrap(langchain.llms.base.BaseLLM, "agenerate")
    unwrap(langchain.chat_models.base.BaseChatModel, "generate")
    unwrap(langchain.chat_models.base.BaseChatModel, "agenerate")
    unwrap(langchain.chains.base.Chain, "__call__")
    unwrap(langchain.chains.base.Chain, "acall")
    for text_embedding_model in text_embedding_models:
        if hasattr(langchain.embeddings, text_embedding_model):
            if isinstance(
                deep_getattr(langchain.embeddings, "%s.embed_query" % text_embedding_model), wrapt.ObjectProxy
            ):
                unwrap(getattr(langchain.embeddings, text_embedding_model), "embed_query")
            if isinstance(
                deep_getattr(langchain.embeddings, "%s.embed_documents" % text_embedding_model), wrapt.ObjectProxy
            ):
                unwrap(getattr(langchain.embeddings, text_embedding_model), "embed_documents")
    for vectorstore in vectorstores:
        if hasattr(langchain.vectorstores, vectorstore):
            if isinstance(
                deep_getattr(langchain.vectorstores, "%s.similarity_search" % vectorstore), wrapt.ObjectProxy
            ):
                unwrap(getattr(langchain.vectorstores, vectorstore), "similarity_search")

    delattr(langchain, "_datadog_integration")
