try:
    import langchain_core
except ImportError:
    langchain_core = None

from ddtrace import Span
from ddtrace.contrib.internal.langchain.utils import _extract_api_key
from ddtrace.contrib.internal.langchain.utils import _extract_model_name
from ddtrace.contrib.internal.langchain.utils import shared_stream
from ddtrace.contrib.internal.langchain.utils import tag_general_message_input
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._integrations.langchain import LangChainIntegration
from ddtrace.llmobs._utils import safe_json


@with_traced_module
def traced_chat_stream(langchain, pin, func, instance, args, kwargs):
    integration: LangChainIntegration = langchain._datadog_integration
    llm_provider = instance._llm_type
    model = _extract_model_name(instance)

    def _on_span_started(span: Span):
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
def traced_chain_stream(langchain, pin, func, instance, args, kwargs):
    integration: LangChainIntegration = langchain._datadog_integration

    def _on_span_started(span: Span):
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
