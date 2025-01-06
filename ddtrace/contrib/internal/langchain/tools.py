import sys

from ddtrace.internal.utils import get_argument_value
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.llmobs._utils import safe_json


@with_traced_module
def traced_base_tool_invoke(langchain, pin, func, instance, args, kwargs):
    integration = langchain._datadog_integration
    tool_input = get_argument_value(args, kwargs, 0, "input")
    config = get_argument_value(args, kwargs, 1, "config", optional=True)

    span = integration.trace(pin, "%s" % func.__self__.name, interface_type="tool", submit_to_llmobs=True)

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

    span = integration.trace(pin, "%s" % func.__self__.name, interface_type="tool", submit_to_llmobs=True)

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
