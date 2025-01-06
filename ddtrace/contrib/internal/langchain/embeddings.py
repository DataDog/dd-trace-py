import sys

from ddtrace.contrib.internal.langchain.utils import _extract_api_key
from ddtrace.contrib.internal.langchain.utils import _extract_model_name
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.internal.utils import ArgumentError
from ddtrace.internal.utils import get_argument_value


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
    )
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
        integration.metric(span, "incr", "request.error", 1)
        raise
    finally:
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=embeddings, operation="embedding")
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
