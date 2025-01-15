import sys
from typing import Any
from typing import Dict

import langchain


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
    from langchain.callbacks.openai_info import get_openai_token_cost_for_model
except ImportError:
    try:
        from langchain_community.callbacks.openai_info import get_openai_token_cost_for_model
    except ImportError:
        get_openai_token_cost_for_model = None


from ddtrace import Span
from ddtrace.contrib.internal.langchain.constants import COMPLETION_TOKENS
from ddtrace.contrib.internal.langchain.constants import MODEL
from ddtrace.contrib.internal.langchain.constants import PROMPT_TOKENS
from ddtrace.contrib.internal.langchain.constants import TOTAL_COST
from ddtrace.contrib.internal.langchain.utils import PATCH_LANGCHAIN_V0
from ddtrace.contrib.internal.langchain.utils import _extract_api_key
from ddtrace.contrib.internal.langchain.utils import _extract_model_name
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value


log = get_logger(__name__)


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
        if _is_openai_llm_instance(instance):
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
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=completions, operation="llm")
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
        submit_to_llmobs=True,
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
        if _is_openai_llm_instance(instance):
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
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=completions, operation="llm")
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
        submit_to_llmobs=True,
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
        if _is_openai_chat_instance(instance):
            _tag_openai_token_usage(span, chat_completions.llm_output)
            integration.record_usage(span, chat_completions.llm_output)

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
        integration.metric(span, "incr", "request.error", 1)
        raise
    finally:
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=chat_completions, operation="chat")
        span.finish()
        integration.metric(span, "dist", "request.duration", span.duration_ns)
        if integration.is_pc_sampled_log(span):
            if chat_completions is None:
                log_chat_completions = []
            else:
                log_chat_completions = [
                    [
                        {"content": message.text, "message_type": message.message.__class__.__name__}
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
                                "content": (
                                    message.get("content", "")
                                    if isinstance(message, dict)
                                    else str(getattr(message, "content", ""))
                                ),
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
        submit_to_llmobs=True,
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
        if _is_openai_chat_instance(instance):
            _tag_openai_token_usage(span, chat_completions.llm_output)
            integration.record_usage(span, chat_completions.llm_output)

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
        integration.metric(span, "incr", "request.error", 1)
        raise
    finally:
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=chat_completions, operation="chat")
        span.finish()
        integration.metric(span, "dist", "request.duration", span.duration_ns)
        if integration.is_pc_sampled_log(span):
            if chat_completions is None:
                log_chat_completions = []
            else:
                log_chat_completions = [
                    [
                        {"content": message.text, "message_type": message.message.__class__.__name__}
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
                                "content": (
                                    message.get("content", "")
                                    if isinstance(message, dict)
                                    else str(getattr(message, "content", ""))
                                ),
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


def _tag_openai_token_usage(
    span: Span, llm_output: Dict[str, Any], propagated_cost: int = 0, propagate: bool = False
) -> None:
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
    if not propagate and get_openai_token_cost_for_model:
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
    if get_openai_token_cost_for_model:
        span.set_metric(TOTAL_COST, propagated_cost + total_cost)
    if span._parent is not None:
        _tag_openai_token_usage(span._parent, llm_output, propagated_cost=propagated_cost + total_cost, propagate=True)


def _is_openai_llm_instance(instance):
    """Safely check if a traced instance is an OpenAI LLM.
    langchain_community does not automatically import submodules which may result in AttributeErrors.
    """
    try:
        if not PATCH_LANGCHAIN_V0 and langchain_openai:
            return isinstance(instance, langchain_openai.OpenAI)
        if not PATCH_LANGCHAIN_V0 and langchain_community:
            return isinstance(instance, langchain_community.llms.OpenAI)
        return isinstance(instance, langchain.llms.OpenAI)
    except (AttributeError, ModuleNotFoundError, ImportError):
        return False


def _is_openai_chat_instance(instance):
    """Safely check if a traced instance is an OpenAI Chat Model.
    langchain_community does not automatically import submodules which may result in AttributeErrors.
    """
    try:
        if not PATCH_LANGCHAIN_V0 and langchain_openai:
            return isinstance(instance, langchain_openai.ChatOpenAI)
        if not PATCH_LANGCHAIN_V0 and langchain_community:
            return isinstance(instance, langchain_community.chat_models.ChatOpenAI)
        return isinstance(instance, langchain.chat_models.ChatOpenAI)
    except (AttributeError, ModuleNotFoundError, ImportError):
        return False
