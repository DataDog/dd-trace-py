import langchain
from langchain.callbacks import get_openai_callback


from ddtrace.vendor.wrapt import wrap_function_wrapper as _w
from ddtrace.internal.utils.wrappers import unwrap as _u
from ddtrace.pin import Pin
from ddtrace.contrib.langchain.constants import text_embedding_models

# TODO: set up rate sampler for prompt-completion tagging?
# TODO: truncate/remove whitespace on prompt/completions?
# TODO: Logging?
# TODO: tag api key?
# TODO: how should we name the spans? Langchain-provided Chains/LLMs vs custom user chain/LLM


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


def traced_llm_generate(wrapped, instance, args, kwargs):
    pin = Pin.get_from(langchain)
    llm_provider = instance._llm_type
    prompts = args[0]
    with pin.tracer.trace("%s.%s" % (instance.__module__, instance.__class__.__name__)) as span:
        if len(prompts) == 1:
            span.set_tag_str("langchain.request.prompt", prompts[0])
        else:
            for idx, prompt in enumerate(prompts):
                span.set_tag_str("langchain.request.prompts.%d" % idx, prompt)
        model = _extract_model_name(instance)
        if model is not None:
            span.set_tag_str("langchain.request.model", model)
        span.set_tag_str("langchain.request.provider", llm_provider)
        for param, val in instance._default_params.items():
            span.set_tag("langchain.%s.parameters.%s" % (llm_provider, param), val)
        if isinstance(instance, langchain.llms.OpenAI):
            with get_openai_callback() as cb:
                completions = wrapped(*args, **kwargs)
            _tag_token_usage(span, cb)
        else:
            completions = wrapped(*args, **kwargs)
        if len(completions.generations) == 1:
            span.set_tag_str("langchain.response.completion.text", completions.generations[0][0].text)
            span.set_tag_str(
                "langchain.response.completions.finish_reason",
                completions.generations[0][0].generation_info.get("finish_reason"),
            )
            span.set_tag(
                "langchain.response.completions.logprobs", completions.generations[0][0].generation_info.get("logprobs")
            )
        else:
            for idx, completion in enumerate(completions.generations):
                span.set_tag_str("langchain.response.completions.%d.text" % idx, completion[0].text)
                span.set_tag_str(
                    "langchain.response.completions.%d.finish_reason" % idx,
                    completion[0].generation_info.get("finish_reason"),
                )
                span.set_tag(
                    "langchain.response.completions.%d.logprobs" % idx, completion[0].generation_info.get("logprobs")
                )
    return completions


async def traced_llm_agenerate(wrapped, instance, args, kwargs):
    pin = Pin.get_from(langchain)
    llm_provider = instance._llm_type
    prompts = args[0]
    with pin.tracer.trace("%s.%s" % (instance.__module__, instance.__class__.__name__)) as span:
        if len(prompts) == 1:
            span.set_tag_str("langchain.request.prompt", prompts[0])
        else:
            for idx, prompt in enumerate(prompts):
                span.set_tag_str("langchain.request.prompts.%d" % idx, prompt)
        model = _extract_model_name(instance)
        if model is not None:
            span.set_tag_str("langchain.request.model", model)
        span.set_tag_str("langchain.request.provider", llm_provider)
        for param, val in instance._default_params.items():
            span.set_tag("langchain.%s.parameters.%s" % (llm_provider, param), val)
        if isinstance(instance, langchain.llms.OpenAI):
            with get_openai_callback() as cb:
                completions = await wrapped(*args, **kwargs)
            _tag_token_usage(span, cb)
        else:
            completions = await wrapped(*args, **kwargs)
        if len(completions.generations) == 1:
            span.set_tag_str("langchain.response.completion.text", completions.generations[0][0].text)
            span.set_tag_str(
                "langchain.response.completions.finish_reason",
                completions.generations[0][0].generation_info.get("finish_reason"),
            )
            span.set_tag(
                "langchain.response.completions.logprobs", completions.generations[0][0].generation_info.get("logprobs")
            )
        else:
            for idx, completion in enumerate(completions.generations):
                span.set_tag_str("langchain.response.completions.%d.text" % idx, completion[0].text)
                span.set_tag_str(
                    "langchain.response.completions.%d.finish_reason" % idx,
                    completion[0].generation_info.get("finish_reason"),
                )
                span.set_tag(
                    "langchain.response.completions.%d.logprobs" % idx, completion[0].generation_info.get("logprobs")
                )
    return completions


def traced_chat_model_generate(wrapped, instance, args, kwargs):
    pin = Pin.get_from(langchain)
    llm_provider = instance._llm_type.split("-")[0]
    chat_messages = args[0]
    with pin.tracer.trace("%s.%s" % (instance.__module__, instance.__class__.__name__)) as span:
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
        for param, val in instance._default_params.items():
            span.set_tag("langchain.%s.parameters.%s" % (llm_provider, param), val)
        if isinstance(instance, langchain.chat_models.ChatOpenAI):
            with get_openai_callback() as cb:
                chat_completions = wrapped(*args, **kwargs)
            _tag_token_usage(span, cb)
        else:
            chat_completions = wrapped(*args, **kwargs)
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


async def traced_chat_model_agenerate(wrapped, instance, args, kwargs):
    pin = Pin.get_from(langchain)
    llm_provider = instance._llm_type.split("-")[0]
    chat_messages = args[0]
    with pin.tracer.trace("%s.%s" % (instance.__module__, instance.__class__.__name__)) as span:
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
        for param, val in instance._default_params.items():
            span.set_tag("langchain.%s.parameters.%s" % (llm_provider, param), val)
        if isinstance(instance, langchain.chat_models.ChatOpenAI):
            with get_openai_callback() as cb:
                chat_completions = await wrapped(*args, **kwargs)
            _tag_token_usage(span, cb)
        else:
            chat_completions = await wrapped(*args, **kwargs)
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


def traced_embedding(wrapped, instance, args, kwargs):
    pin = Pin.get_from(langchain)
    input_texts = args[0]
    with pin.tracer.trace("%s.%s" % (instance.__module__, instance.__class__.__name__)) as span:
        if isinstance(input_texts, str):
            span.set_tag_str("langchain.request.text", input_texts)
            span.set_metric("langchain.request.input_count", 1)
        else:
            for idx, text in enumerate(input_texts):
                span.set_tag_str("langchain.request.inputs.%d.text" % idx, text)
            span.set_metric("langchain.request.input_count", len(input_texts))
        span.set_tag_str("langchain.request.model", instance.model)
        # langchain currently does not support token tracking for OpenAI embeddings:
        #  https://github.com/hwchase17/langchain/issues/945
        embeddings = wrapped(*args, **kwargs)
        if isinstance(embeddings, list):
            for idx, embedding in enumerate(embeddings):
                span.set_metric("langchain.response.outputs.%d.embedding_length" % idx, len(embedding))
        else:
            span.set_metric("langchain.response.outputs.embedding_length" % idx, len(embeddings))
        return embeddings


def traced_chain_call(wrapped, instance, args, kwargs):
    pin = Pin.get_from(langchain)
    with pin.tracer.trace("%s.%s" % (instance.__module__, instance.__class__.__name__)) as span:
        inputs = args[0]
        if isinstance(inputs, dict):
            for k, v in inputs.items():
                span.set_tag("langchain.request.inputs.%s" % k, v)
        else:
            span.set_tag_str("langchain.request.inputs.%s" % instance.input_keys[0], inputs)
        final_outputs = wrapped(*args, **kwargs)
        # import pdb; pdb.set_trace()
        for k, v in final_outputs.items():
            span.set_tag_str("langchain.response.outputs.%s" % k, str(v))
        pass

    return final_outputs


async def traced_chain_acall(wrapped, instance, args, kwargs):
    pin = Pin.get_from(langchain)
    with pin.tracer.trace("%s.%s" % (instance.__module__, instance.__class__.__name__)) as span:
        inputs = args[0]
        if isinstance(inputs, dict):
            for k, v in inputs.items():
                span.set_tag("langchain.request.inputs.%s" % k, v)
        else:
            span.set_tag_str("langchain.request.inputs.%s" % instance.input_keys[0], inputs)
        final_outputs = await wrapped(*args, **kwargs)
        for k, v in final_outputs.items():
            span.set_tag_str("langchain.response.outputs.%s" % k, str(v))
        pass

    return final_outputs


def patch():
    if getattr(langchain, "_datadog_patch", False):
        return
    setattr(langchain, "_datadog_patch", True)

    Pin().onto(langchain)

    # TODO: check if we need to version gate LLM/Chat/TextEmbedding
    _w("langchain", "llms.BaseLLM.generate", traced_llm_generate)
    _w("langchain", "llms.BaseLLM.agenerate", traced_llm_agenerate)
    _w("langchain", "chat_models.base.BaseChatModel.generate", traced_chat_model_generate)
    _w("langchain", "chat_models.base.BaseChatModel.agenerate", traced_chat_model_agenerate)
    # Text embedding models override two abstract base methods instead of super calls, so we need to
    #  wrap each langchain-provided text embedding models.
    for text_embedding_model in text_embedding_models:
        _w("langchain", "embeddings.%s.embed_query" % text_embedding_model, traced_embedding)
        _w("langchain", "embeddings.%s.embed_documents" % text_embedding_model, traced_embedding)
    _w("langchain", "chains.base.Chain.__call__", traced_chain_call)
    _w("langchain", "chains.base.Chain.acall", traced_chain_acall)


def unpatch():
    if getattr(langchain, "_datadog_patch", False):
        setattr(langchain, "_datadog_patch", False)

    _u(langchain.llms.BaseLLM, "generate")
    _u(langchain.llms.BaseLLM, "agenerate")
    _u(langchain.chat_models.BaseChatModel, "generate")
    _u(langchain.chat_models.BaseChatModel, "agenerate")
    for text_embedding_model in text_embedding_models:
        _u(getattr(langchain.embeddings, text_embedding_model), "traced_embed_query")
        _u(getattr(langchain.embeddings, text_embedding_model), "traced_embed_documents")
    _u(langchain.chains.base.Chain, "__call__")
    _u(langchain.chains.base.Chain, "acall")
