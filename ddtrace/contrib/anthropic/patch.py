import json
import os
import sys
from typing import Any
from typing import Iterable
from typing import Optional
from typing import Union

import anthropic
from pydantic import SecretStr

from ddtrace import config
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._integrations import AnthropicIntegration
from ddtrace.pin import Pin


log = get_logger(__name__)


def get_version():
    # type: () -> str
    return getattr(anthropic, "__version__", "")


ANTHROPIC_VERSION = parse_version(get_version())
BASE_MODULE = anthropic
BASE_MODULE_NAME = getattr(anthropic, "__name__", "anthropic")


config._add(
    "anthropic",
    {
        "span_prompt_completion_sample_rate": float(os.getenv("DD_ANTHROPIC_SPAN_PROMPT_COMPLETION_SAMPLE_RATE", 1.0)),
        "span_char_limit": int(os.getenv("DD_ANTHROPIC_SPAN_CHAR_LIMIT", 128)),
    },
)


def _extract_model_name(kwargs: Any) -> Optional[str]:
    """Extract model name or ID from llm instance."""
    return kwargs.get("model", "")


def _format_api_key(api_key: Union[str, SecretStr]) -> str:
    """Obfuscate a given LLM provider API key by returning the last four characters."""
    if not api_key or len(api_key) < 4:
        return ""
    return "...%s" % api_key[-4:]


def _extract_api_key(instance: Any) -> str:
    """
    Extract and format LLM-provider API key from instance.
    """
    api_key = getattr(getattr(instance, "_client", ""), "api_key", None)
    if api_key:
        return _format_api_key(api_key)
    return ""


# def _tag_anthropic_token_usage(
#     span: Span, llm_output: Dict[str, Any], propagated_cost: int = 0, propagate: bool = False
# ) -> None:
#     """
#     Extract token usage from llm_output, tag on span.
#     Calculate the total cost for each LLM/chat_model, then propagate those values up the trace so that
#     the root span will store the total token_usage/cost of all of its descendants.
#     """
#     for token_type in ("input", "output"):
#         current_metric_value = span.get_metric("anthropic.tokens.%s_tokens" % token_type) or 0
#         metric_value = llm_output["token_usage"].get("%s_tokens" % token_type, 0)
#         span.set_metric("anthropic.tokens.%s_tokens" % token_type, current_metric_value + metric_value)
#     total_cost = span.get_metric(TOTAL_COST) or 0
#     if not propagate:
#         try:
#             completion_cost = get_anthropic_token_cost_for_model(
#                 span.get_tag(MODEL),
#                 span.get_metric(COMPLETION_TOKENS),
#                 is_completion=True,
#             )
#             prompt_cost = get_anthropic_token_cost_for_model(span.get_tag(MODEL), span.get_metric(PROMPT_TOKENS))
#             total_cost = completion_cost + prompt_cost
#         except ValueError:
#             # If not in Anthropic's model catalog, the above helpers will raise a ValueError.
#             log.debug("Cannot calculate token/cost as the model is not in Anthropic's model catalog.")
#     span.set_metric(TOTAL_COST, propagated_cost + total_cost)
#     if span._parent is not None:
#         _tag_anthropic_token_usage(span._parent, llm_output,
# propagated_cost=propagated_cost + total_cost, propagate=True)


@with_traced_module
def traced_chat_model_generate(anthropic, pin, func, instance, args, kwargs):
    chat_messages = get_argument_value(args, kwargs, 0, "messages")
    integration = anthropic._datadog_integration

    span = integration.trace(
        pin,
        "%s.%s" % (instance.__module__, instance.__class__.__name__),
        submit_to_llmobs=True,
        interface_type="chat_model",
        provider="anthropic",
        model=_extract_model_name(kwargs),
        api_key=_extract_api_key(instance),
    )
    chat_completions = None
    try:
        for message_set_idx, message_set in enumerate(chat_messages):
            if isinstance(message_set, dict):
                if isinstance(message_set.get("content", None), str):
                    span.set_tag_str(
                        "anthropic.request.messages.%d.0.content" % (message_set_idx),
                        integration.trunc(message_set.get("content", "")),
                    )
                    span.set_tag_str(
                        "anthropic.request.messages.%d.0.message_type" % (message_set_idx),
                        "text",
                    )
                elif isinstance(message_set.get("content", None), Iterable):
                    for message_idx, message in enumerate(message_set.get("content", [])):
                        if integration.is_pc_sampled_span(span):
                            if message.get("type", None) == "text":
                                span.set_tag_str(
                                    "anthropic.request.messages.%d.%d.content" % (message_set_idx, message_idx),
                                    integration.trunc(str(message.get("text", ""))),
                                )
                            elif message.get("type", None) == "image":
                                span.set_tag_str(
                                    "anthropic.request.messages.%d.%d.content" % (message_set_idx, message_idx),
                                    integration.trunc(json.dumps(message.get("source", ""))),
                                )

                        span.set_tag_str(
                            "anthropic.request.messages.%d.%d.message_type" % (message_set_idx, message_idx),
                            message.get("type", "text"),
                        )
        for param, val in kwargs.items():
            if param != "messages":
                if isinstance(val, dict):
                    for k, v in val.items():
                        span.set_tag_str("anthropic.request.parameters.%s.%s" % (param, k), str(v))
                else:
                    span.set_tag_str("anthropic.request.parameters.%s" % (param), str(val))
        chat_completions = func(*args, **kwargs)

        #     _tag_anthropic_token_usage(span, chat_completions.llm_output)
        #     integration.record_usage(span, chat_completions.llm_output)

        for idx, chat_completion in enumerate(chat_completions.content):
            if integration.is_pc_sampled_span(span):
                span.set_tag_str(
                    "anthropic.response.completions.%d.content" % (idx),
                    integration.trunc(chat_completion.text),
                )
            span.set_tag_str(
                "anthropic.response.completions.%d.message_type" % (idx),  # TO-DO: change to .type?
                chat_completion.type,
            )
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        # if integration.is_pc_sampled_llmobs(span):
        integration.llmobs_set_tags(span, chat_messages, chat_completions, err=bool(span.error))
        span.finish()
        # integration.metric(span, "dist", "request.duration", span.duration_ns)
    return chat_completions


def patch():
    if getattr(anthropic, "_datadog_patch", False):
        return

    anthropic._datadog_patch = True

    Pin().onto(anthropic)
    integration = AnthropicIntegration(integration_config=config.anthropic)
    anthropic._datadog_integration = integration

    wrap("anthropic", "resources.messages.Messages.create", traced_chat_model_generate(anthropic))
    # wrap("langchain", "chat_models.base.BaseChatModel.agenerate", traced_chat_model_agenerate(langchain))

    # if _is_iast_enabled():
    #     from ddtrace.appsec._iast._metrics import _set_iast_error_metric

    #     def wrap_output_parser(module, parser):
    #         # Ensure not double patched
    #         if not isinstance(deep_getattr(module, "%s.parse" % parser), wrapt.ObjectProxy):
    #             wrap(module, "%s.parse" % parser, taint_parser_output)

    #     try:
    #         with_agent_output_parser(wrap_output_parser)
    #     except Exception as e:
    #         _set_iast_error_metric("IAST propagation error. langchain wrap_output_parser. {}".format(e))


def unpatch():
    if not getattr(anthropic, "_datadog_patch", False):
        return

    anthropic._datadog_patch = False

    unwrap(anthropic.resources.messages.Messages, "create")
    # unwrap(langchain.chat_models.base.BaseChatModel, "agenerate")

    delattr(anthropic, "_datadog_integration")


def taint_outputs(instance, inputs, outputs):
    from ddtrace.appsec._iast._metrics import _set_iast_error_metric
    from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
    from ddtrace.appsec._iast._taint_tracking import taint_pyobject

    try:
        ranges = None
        for key in filter(lambda x: x in inputs, instance.input_keys):
            input_val = inputs.get(key)
            if input_val:
                ranges = get_tainted_ranges(input_val)
                if ranges:
                    break

        if ranges:
            source = ranges[0].source
            for key in filter(lambda x: x in outputs, instance.output_keys):
                output_value = outputs[key]
                outputs[key] = taint_pyobject(output_value, source.name, source.value, source.origin)
    except Exception as e:
        _set_iast_error_metric("IAST propagation error. langchain taint_outputs. {}".format(e))


# def taint_parser_output(func, instance, args, kwargs):
#     from ddtrace.appsec._iast._metrics import _set_iast_error_metric
#     from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
#     from ddtrace.appsec._iast._taint_tracking import taint_pyobject

#     result = func(*args, **kwargs)
#     try:
#         try:
#             from langchain_core.agents import AgentAction
#             from langchain_core.agents import AgentFinish
#         except ImportError:
#             from langchain.agents import AgentAction
#             from langchain.agents import AgentFinish
#         ranges = get_tainted_ranges(args[0])
#         if ranges:
#             source = ranges[0].source
#             if isinstance(result, AgentAction):
#                 result.tool_input = taint_pyobject(result.tool_input, source.name, source.value, source.origin)
#             elif isinstance(result, AgentFinish) and "output" in result.return_values:
#                 values = result.return_values
#                 values["output"] = taint_pyobject(values["output"], source.name, source.value, source.origin)
#     except Exception as e:
#         _set_iast_error_metric("IAST propagation error. langchain taint_parser_output. {}".format(e))

#     return result


# def with_agent_output_parser(f):
#     import langchain.agents

#     queue = [(langchain.agents, agent_output_parser_classes)]

#     while len(queue) > 0:
#         module, current = queue.pop(0)
#         if isinstance(current, str):
#             if hasattr(module, current):
#                 f(module, current)
#         elif isinstance(current, dict):
#             for name, value in current.items():
#                 if hasattr(module, name):
#                     queue.append((getattr(module, name), value))
