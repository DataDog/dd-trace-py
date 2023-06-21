import os

import langchain
from langchain.callbacks import get_openai_callback

from ddtrace import config
from ddtrace.pin import Pin
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.contrib.langchain.constants import text_embedding_models
from ddtrace.contrib.openai._logging import V2LogWriter
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.internal.dogstatsd import get_dogstatsd_client
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.formats import asbool
from ddtrace.sampler import RateSampler


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


class _LangChainIntegration:
    def __init__(self, config, stats_url, site, api_key):
        # FIXME: this currently does not consider if the tracer is configured to
        # use a different hostname. eg. tracer.configure(host="new-hostname")
        # Ideally the metrics client should live on the tracer or some other core
        # object that is strongly linked with configuration.
        self._statsd = get_dogstatsd_client(stats_url, namespace="openai")
        self._config = config
        self._log_writer = V2LogWriter(
            site=site,
            api_key=api_key,
            interval=float(os.getenv("_DD_OPENAI_LOG_WRITER_INTERVAL", "1.0")),
            timeout=float(os.getenv("_DD_OPENAI_LOG_WRITER_TIMEOUT", "2.0")),
        )
        self._span_pc_sampler = RateSampler(sample_rate=config.span_prompt_completion_sample_rate)
        self._log_pc_sampler = RateSampler(sample_rate=config.log_prompt_completion_sample_rate)


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
    # TODO: can we add the state to the instance?
    llm_provider = instance._llm_type
    prompts = args[0]
    with pin.tracer.trace(
        "langchain.request", resource="%s.%s" % (instance.__module__, instance.__class__.__name__)
    ) as span:
        # Enable trace metrics for these spans so users can see per-service openai usage in APM.
        span.set_tag(SPAN_MEASURED_KEY)
        if len(prompts) == 1:
            span.set_tag_str("langchain.request.prompt", prompts[0])
        else:
            for idx, prompt in enumerate(prompts):
                span.set_tag_str("langchain.request.prompts.%d" % idx, prompt)
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
        if len(completions.generations) == 1:
            span.set_tag_str("langchain.response.completion.text", completions.generations[0][0].text)
            span.set_tag_str(
                "langchain.response.completion.finish_reason",
                completions.generations[0][0].generation_info.get("finish_reason"),
            )
            span.set_tag_str(
                "langchain.response.completion.logprobs",
                str(completions.generations[0][0].generation_info.get("logprobs")),
            )
        else:
            for idx, completion in enumerate(completions.generations):
                span.set_tag_str("langchain.response.completions.%d.text" % idx, completion[0].text)
                span.set_tag_str(
                    "langchain.response.completions.%d.finish_reason" % idx,
                    completion[0].generation_info.get("finish_reason"),
                )
                span.set_tag_str(
                    "langchain.response.completions.%d.logprobs" % idx,
                    str(completion[0].generation_info.get("logprobs")),
                )
    return completions


@with_traced_module
async def traced_llm_agenerate(langchain, pin, func, instance, args, kwargs):
    llm_provider = instance._llm_type
    prompts = args[0]
    with pin.tracer.trace(
        "langchain.request", resource="%s.%s" % (instance.__module__, instance.__class__.__name__)
    ) as span:
        # Enable trace metrics for these spans so users can see per-service openai usage in APM.
        span.set_tag(SPAN_MEASURED_KEY)
        if len(prompts) == 1:
            span.set_tag_str("langchain.request.prompt", prompts[0])
        else:
            for idx, prompt in enumerate(prompts):
                span.set_tag_str("langchain.request.prompts.%d" % idx, prompt)
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
        if len(completions.generations) == 1:
            span.set_tag_str("langchain.response.completion.text", completions.generations[0][0].text)
            span.set_tag_str(
                "langchain.response.completion.finish_reason",
                completions.generations[0][0].generation_info.get("finish_reason"),
            )
            span.set_tag_str(
                "langchain.response.completion.logprobs",
                str(completions.generations[0][0].generation_info.get("logprobs")),
            )
        else:
            for idx, completion in enumerate(completions.generations):
                span.set_tag_str("langchain.response.completions.%d.text" % idx, completion[0].text)
                span.set_tag_str(
                    "langchain.response.completions.%d.finish_reason" % idx,
                    completion[0].generation_info.get("finish_reason"),
                )
                span.set_tag_str(
                    "langchain.response.completions.%d.logprobs" % idx,
                    str(completion[0].generation_info.get("logprobs")),
                )
    return completions


@with_traced_module
def traced_chat_model_generate(langchain, pin, func, instance, args, kwargs):
    llm_provider = instance._llm_type.split("-")[0]
    chat_messages = args[0]
    with pin.tracer.trace(
        "langchain.request", resource="%s.%s" % (instance.__module__, instance.__class__.__name__)
    ) as span:
        # Enable trace metrics for these spans so users can see per-service openai usage in APM.
        span.set_tag(SPAN_MEASURED_KEY)
        for message_set_idx, message_set in enumerate(chat_messages):
            for message_idx, message in enumerate(message_set):
                span.set_tag_str(
                    "langchain.request.messages.%d.%d.content" % (message_set_idx, message_idx), message.content
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
                span.set_tag_str(
                    "langchain.response.completions.%d.%d.content" % (message_set_idx, idx), chat_completion.text
                )
                span.set_tag_str(
                    "langchain.response.completions.%d.%d.message_type" % (message_set_idx, idx),
                    chat_completion.message.__class__.__name__,
                )

    return chat_completions


@with_traced_module
async def traced_chat_model_agenerate(langchain, pin, func, instance, args, kwargs):
    llm_provider = instance._llm_type.split("-")[0]
    chat_messages = args[0]
    with pin.tracer.trace(
        "langchain.request", resource="%s.%s" % (instance.__module__, instance.__class__.__name__)
    ) as span:
        # Enable trace metrics for these spans so users can see per-service openai usage in APM.
        span.set_tag(SPAN_MEASURED_KEY)
        for message_set_idx, message_set in enumerate(chat_messages):
            for message_idx, message in enumerate(message_set):
                span.set_tag_str(
                    "langchain.request.messages.%d.%d.content" % (message_set_idx, message_idx), message.content
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
                span.set_tag_str(
                    "langchain.response.completions.%d.%d.content" % (message_set_idx, idx), chat_completion.text
                )
                span.set_tag_str(
                    "langchain.response.completions.%d.%d.message_type" % (message_set_idx, idx),
                    chat_completion.message.__class__.__name__,
                )
    return chat_completions


@with_traced_module
def traced_embedding(langchain, pin, func, instance, args, kwargs):
    input_texts = args[0]
    provider = instance.__class__.__name__.split("Embeddings")[0].lower()
    with pin.tracer.trace(
        "langchain.request", resource="%s.%s" % (instance.__module__, instance.__class__.__name__)
    ) as span:
        # Enable trace metrics for these spans so users can see per-service openai usage in APM.
        span.set_tag(SPAN_MEASURED_KEY)
        if isinstance(input_texts, str):
            span.set_tag_str("langchain.request.inputs.0.text", input_texts)
            span.set_metric("langchain.request.input_count", 1)
        else:
            for idx, text in enumerate(input_texts):
                span.set_tag_str("langchain.request.inputs.%d.text" % idx, text)
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
        return embeddings


@with_traced_module
def traced_chain_call(langchain, pin, func, instance, args, kwargs):
    with pin.tracer.trace(
        "langchain.request", resource="%s.%s" % (instance.__module__, instance.__class__.__name__)
    ) as span:
        # Enable trace metrics for these spans so users can see per-service openai usage in APM.
        span.set_tag(SPAN_MEASURED_KEY)
        inputs = args[0]
        if isinstance(inputs, dict):
            for k, v in inputs.items():
                span.set_tag_str("langchain.request.inputs.%s" % k, str(v))
        else:
            span.set_tag_str("langchain.request.inputs.%s" % instance.input_keys[0], inputs)
        if hasattr(instance, "prompt"):
            span.set_tag_str("langchain.request.prompt", str(instance.prompt.template))
        final_outputs = func(*args, **kwargs)
        for k, v in final_outputs.items():
            span.set_tag_str("langchain.response.outputs.%s" % k, str(v))
        pass

    return final_outputs


@with_traced_module
async def traced_chain_acall(langchain, pin, func, instance, args, kwargs):
    with pin.tracer.trace(
        "langchain.request", resource="%s.%s" % (instance.__module__, instance.__class__.__name__)
    ) as span:
        # Enable trace metrics for these spans so users can see per-service openai usage in APM.
        span.set_tag(SPAN_MEASURED_KEY)
        inputs = args[0]
        if isinstance(inputs, dict):
            for k, v in inputs.items():
                span.set_tag_str("langchain.request.inputs.%s" % k, str(v))
        else:
            span.set_tag_str("langchain.request.inputs.%s" % instance.input_keys[0], inputs)
        if hasattr(instance, "prompt"):
            span.set_tag_str("langchain.request.prompt", str(instance.prompt.template))
        final_outputs = await func(*args, **kwargs)
        for k, v in final_outputs.items():
            span.set_tag_str("langchain.response.outputs.%s" % k, str(v))
        pass

    return final_outputs


@with_traced_module
def traced_similarity_search(langchain, pin, func, instance, args, kwargs):
    return func(*args, **kwargs)


def patch():
    if getattr(langchain, "_datadog_patch", False):
        return
    setattr(langchain, "_datadog_patch", True)

    # TODO: can we just share the _OpenAIIntegration class with LangChain?
    #  Note that this would mean we need to share the integration object with everything in
    #  langchain since it's top level
    #  How do we test this? Can we mock out the metric/logger/sampler?
    # setattr(langchain, "_langchain_integration", _LangChainIntegration())

    Pin().onto(langchain)

    ddapikey = os.getenv("DD_API_KEY", config.langchain._api_key)

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
