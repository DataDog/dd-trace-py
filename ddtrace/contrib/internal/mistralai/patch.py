import sys

import mistralai

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import with_traced_module
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.llmobs._integrations.mistralai import MistralAIIntegration
from ddtrace.trace import Pin


def get_version() -> str:
    return getattr(mistralai, "__version__", "")


config._add("mistralai", {})


@with_traced_module
def traced_conversations_start(mistralai, pin, func, instance, args, kwargs):
    integration = mistralai._datadog_integration
    result = None
    span = integration.trace(
        pin, "conversations.start", span_name=getattr(instance, "name", ""), operation="agent", submit_to_llmobs=True
    )
    try:
        result = func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation="agent")
        span.finish()
    return result


@with_traced_module
def traced_conversations_append(mistralai, pin, func, instance, args, kwargs):
    integration = mistralai._datadog_integration
    result = None
    span = integration.trace(
        pin, "conversations.append", span_name=getattr(instance, "name", ""), operation="agent", submit_to_llmobs=True
    )
    try:
        result = func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation="agent")
        span.finish()
    return result


def patch():
    if getattr(mistralai, "_datadog_patch", False):
        return

    mistralai._datadog_patch = True

    Pin().onto(mistralai)
    integration = MistralAIIntegration(integration_config=config.mistralai)
    mistralai._datadog_integration = integration

    # start / append are the main methods to interact with agent runs
    wrap(mistralai, "conversations.Conversations.start", traced_conversations_start(mistralai))
    wrap(mistralai, "conversations.Conversations.append", traced_conversations_append(mistralai))


def unpatch():
    if not getattr(mistralai, "_datadog_patch", False):
        return

    mistralai._datadog_patch = False

    unwrap(mistralai, "client.beta.conversations.start")
    unwrap(mistralai, "client.beta.conversations.append")

    delattr(mistralai, "_datadog_integration")
