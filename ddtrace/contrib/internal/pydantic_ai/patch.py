import sys

import pydantic_ai

from ddtrace import config
from ddtrace.contrib._events.llm import LlmRequestEvent
from ddtrace.contrib.internal.pydantic_ai._constants import AGENT_SPAN_NAME
from ddtrace.contrib.internal.pydantic_ai._constants import COMPONENT
from ddtrace.contrib.internal.pydantic_ai._constants import TAG_MODEL
from ddtrace.contrib.internal.pydantic_ai._constants import TAG_PROVIDER
from ddtrace.contrib.internal.pydantic_ai._constants import TOOL_SPAN_NAME
from ddtrace.contrib.internal.pydantic_ai.utils import TracedPydanticAsyncContextManager
from ddtrace.contrib.internal.pydantic_ai.utils import TracedPydanticRunStream
from ddtrace.contrib.internal.pydantic_ai.utils import finish_request
from ddtrace.contrib.internal.pydantic_ai.utils import get_model_and_provider
from ddtrace.contrib.internal.trace_utils import int_service
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.internal import core
from ddtrace.internal import integration_hooks
from ddtrace.internal.span_bus import span_from_context
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.version import parse_version


config._add("pydantic_ai", {})


def get_version() -> str:
    import pydantic_ai

    return getattr(pydantic_ai, "__version__", "0.0.0")


def _supported_versions() -> dict[str, str]:
    return {"pydantic_ai": "*"}


PYDANTIC_AI_VERSION = parse_version(get_version())


# True when the latest agent span was started by run_stream, which calls iter internally.
_run_stream_active = False


def _agent_resource(instance) -> str:
    return getattr(instance, "name", None) or "Pydantic Agent"


def _request_event(resource, span_name, operation, args, kwargs, model=None) -> LlmRequestEvent:
    tags = {}
    model_name, provider = "", None
    if model:
        model_name, provider = get_model_and_provider(model)
        tags[TAG_MODEL] = model_name
        if provider:
            tags[TAG_PROVIDER] = provider
    event = LlmRequestEvent(
        component=COMPONENT,
        integration_config=config.pydantic_ai,
        service=int_service(None, config.pydantic_ai),
        resource=resource,
        provider=provider or "",
        model=model_name,
        tags=tags,
        submit_to_llmobs=True,
        request_args=args,
        # The same dict as the call's kwargs: the instance and the streamed run result are added
        # to it once the call returns, for LLMObs to read when the request finishes.
        request_kwargs=kwargs,
        operation=operation,
    )
    event.operation_name = span_name
    return event


def _start_agent_request(instance, args, kwargs):
    event = _request_event(
        _agent_resource(instance), AGENT_SPAN_NAME, "agent", args, kwargs, model=getattr(instance, "model", None)
    )
    # The returned wrapper dispatches the ended event once the run is exited or its output consumed.
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        return ctx


def traced_agent_run_stream(func, instance, args, kwargs):
    global _run_stream_active
    _run_stream_active = True
    ctx = _start_agent_request(instance, args, kwargs)

    result = func(*args, **kwargs)
    kwargs["instance"] = instance
    return TracedPydanticRunStream(result, ctx)


def traced_agent_iter(func, instance, args, kwargs):
    global _run_stream_active
    # avoid double tracing if run_stream has already been called
    if _run_stream_active:
        _run_stream_active = False
        return func(*args, **kwargs)
    ctx = _start_agent_request(instance, args, kwargs)

    result = func(*args, **kwargs)
    kwargs["instance"] = instance
    return TracedPydanticAsyncContextManager(result, ctx)


async def traced_tool_manager_call(func, instance, args, kwargs):
    tool_call = get_argument_value(args, kwargs, 0, "tool_call", True)
    tool_name = getattr(tool_call, "tool_name", None) or "Pydantic Tool"
    tool_manager_tools = getattr(instance, "tools", {}) or {}
    tool_instance = tool_manager_tools.get(tool_name) or None
    return await traced_tool_run(func, tool_instance, args, kwargs, tool_name)


async def traced_execute_tool_call(func, instance, args, kwargs):
    validated = get_argument_value(args, kwargs, 0, "validated", True)
    if validated is None:
        return await func(*args, **kwargs)
    # skip output tools — they are used for structured output, not user-defined function tools
    tool_instance = getattr(validated, "tool", None)
    tool_def = getattr(tool_instance, "tool_def", None)
    if tool_def and getattr(tool_def, "kind", None) == "output":
        return await func(*args, **kwargs)
    tool_call_part = getattr(validated, "call", None)
    tool_name = getattr(tool_call_part, "tool_name", None) or "Pydantic Tool"
    return await traced_tool_run(func, tool_instance, args, kwargs, tool_name)


async def traced_tool_call(func, instance, args, kwargs):
    tool_name = getattr(instance, "name", None) or "Pydantic Tool"
    return await traced_tool_run(func, instance, args, kwargs, tool_name)


async def traced_tool_run(func, instance, args, kwargs, tool_name):
    event = _request_event(tool_name, TOOL_SPAN_NAME, "tool", args, kwargs)
    with core.context_with_event(event, dispatch_end_event=False) as ctx:
        resp = None
        try:
            resp = await func(*args, **kwargs)
            return resp
        except Exception:
            span_from_context(ctx).set_exc_info(*sys.exc_info())
            raise
        finally:
            kwargs["instance"] = instance
            finish_request(ctx, resp)


def patch():
    if getattr(pydantic_ai, "_datadog_patch", False):
        return

    pydantic_ai._datadog_patch = True

    wrap(pydantic_ai, "agent.Agent.iter", traced_agent_iter)
    wrap(pydantic_ai, "agent.Agent.run_stream", traced_agent_run_stream)
    if PYDANTIC_AI_VERSION >= (1, 63, 0):
        wrap(pydantic_ai, "agent.ToolManager.execute_tool_call", traced_execute_tool_call)
    elif PYDANTIC_AI_VERSION >= (0, 4, 4):
        wrap(pydantic_ai, "agent.ToolManager.handle_call", traced_tool_manager_call)
    else:
        wrap(pydantic_ai, "tools.Tool.run", traced_tool_call)

    # Let products (LLMObs) attach their own LlmEvents subscribers without this
    # module importing them.
    integration_hooks.on_patch(COMPONENT)


def unpatch():
    import pydantic_ai

    if not getattr(pydantic_ai, "_datadog_patch", False):
        return

    pydantic_ai._datadog_patch = False

    integration_hooks.on_unpatch(COMPONENT)

    unwrap(pydantic_ai.agent.Agent, "iter")
    unwrap(pydantic_ai.agent.Agent, "run_stream")
    if PYDANTIC_AI_VERSION >= (1, 63, 0):
        unwrap(pydantic_ai.agent.ToolManager, "execute_tool_call")
    elif PYDANTIC_AI_VERSION >= (0, 4, 4):
        unwrap(pydantic_ai.agent.ToolManager, "handle_call")
    else:
        unwrap(pydantic_ai.tools.Tool, "run")
