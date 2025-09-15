"""Instrument google-adk."""
from typing import Dict

import google.adk as adk

from ddtrace import config
from ddtrace import tracer as _tracer
from ddtrace._trace._limits import TRUNCATED_SPAN_ATTRIBUTE_LEN
from ddtrace._trace.pin import Pin
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger


logger = get_logger(__name__)

config._add("google_adk", {"service": "google-adk"})


def _supported_versions()-> Dict[str, str]:
    return {"google.adk": ">=1.0.0"}


def get_version() -> str:
    return getattr(adk, "__version__", "")


def _truncate_value(value):
    try:
        s = str(value)
    except Exception:
        s = repr(value)
    if len(s) > TRUNCATED_SPAN_ATTRIBUTE_LEN:
        return s[:TRUNCATED_SPAN_ATTRIBUTE_LEN]
    return s

CODE_EXECUTOR_CLASSES = [
    # "BuiltInCodeExecutor", # make an external llm tool call to use the llms built in code executor
    "VertexAiCodeExecutor",
    "UnsafeLocalCodeExecutor",
    # "ContainerCodeExecutor", # additional package dependendy
]

MEMORY_SERVICE_CLASSES = [
    "InMemoryMemoryService",
    "VertexAiMemoryBankService",
    "VertexAiRagMemoryService",
]


def patch():
    """Patch the `google.adk` library for tracing."""

    if getattr(adk, "_datadog_patch", False):
        return

    setattr(adk, "_datadog_patch", True)
    Pin().onto(adk)

    # Agent entrypoints (async generators)
    wrap("google.adk", "runners.Runner.run_async", _traced_agent_run_async)
    wrap("google.adk", "runners.Runner.run_live", _traced_agent_run_live)

    # Tool execution (central dispatch)
    wrap("google.adk", "flows.llm_flows.functions.__call_tool_async", _traced_functions_call_tool_async)
    wrap("google.adk", "flows.llm_flows.functions.__call_tool_live", _traced_functions_call_tool_live)

    for code_executor in CODE_EXECUTOR_CLASSES:
        wrap(
            "google.adk",
            f"code_executors.{code_executor}.execute_code",
            _traced_code_executor_execute_code,
        )

    # Plugin callbacks orchestration, TODO: do we want to trace callbacks?
    wrap(
        "google.adk",
        "plugins.plugin_manager.PluginManager._run_callbacks",
        _traced_plugin_manager_run_callbacks,
    )

    for memory_service in MEMORY_SERVICE_CLASSES:
        try:
            wrap(
                "google.adk",
                f"memory.{memory_service}.add_session_to_memory",
                _traced_memory_add_session_async,
            )
            wrap(
                "google.adk",
                f"memory.{memory_service}.search_memory",
                _traced_memory_search_async,
            )
        except Exception:
            logger.exception("Failed to wrap:", memory_service)
            pass


def unpatch():
    """Unpatch the `google.adk` library."""
    if not hasattr(adk, "_datadog_patch") or not getattr(adk, "_datadog_patch"):
        return
    setattr(adk, "_datadog_patch", False)

    unwrap(adk.runners.Runner, "run_async")
    unwrap(adk.runners.Runner, "run_live")

    unwrap(adk.flows.llm_flows.functions, "__call_tool_async")
    unwrap(adk.flows.llm_flows.functions, "__call_tool_live")

    for code_executor in CODE_EXECUTOR_CLASSES:
        unwrap(getattr(adk.code_executors, code_executor), "execute_code")

    unwrap(adk.plugins.plugin_manager.PluginManager, "_run_callbacks")

    for memory_service in MEMORY_SERVICE_CLASSES:
        try:
            unwrap(getattr(adk.memory, memory_service), "add_session_to_memory")
            unwrap(getattr(adk.memory, memory_service), "search_memory")
        except Exception:
            logger.exception("Failed to unwrap:", memory_service)
            pass


def _get_tracer_and_service(instance):
    pin = Pin.get_from(instance)
    if pin and getattr(pin, "enabled", True):
        return pin.tracer, pin.service
    return _tracer, "google-adk"


def _traced_agent_run_async(wrapped, instance, args, kwargs):
    """Trace the main execution of an agent (async generator)."""
    tracer, service = _get_tracer_and_service(instance)
    agen = wrapped(*args, **kwargs)
    fn_kwargs = kwargs

    async def _generator():
        with tracer.trace("google_adk.agent.run", service=service) as span:
            span.set_tag_str("component", "google-adk")
            span.set_tag_str("span.kind", "internal")

            span.set_tag_str("agent_name", instance.agent.name)
            span.set_tag_str("agent_instructions", instance.agent.instruction)
            span.set_tag_str("model_configuration", str(instance.agent.model_config))
            # span.set_tag_str("max_iterations", instance.agent.run_config.max_llm_calls)

            if fn_kwargs.get("session_id"):
                span.set_tag_str("session_management.session_id", fn_kwargs.get("session_id"))
            if fn_kwargs.get("user_id"):
                span.set_tag_str("session_management.user_id", fn_kwargs.get("user_id"))

            for i, tool in enumerate(instance.agent.tools):
                span.set_tag_str(f"tool.[{i}].name", tool.name)
                span.set_tag_str(f"tool.[{i}].description", tool.description)
                for j, arg in enumerate(tool._get_mandatory_args()):
                    span.set_tag_str(f"tool.[{i}].parameters.[{j}]", arg)

            try:
                async for event in agen:
                    yield event
            except Exception as e:
                span.set_exc_info(type(e), e, e.__traceback__)
                raise

    return _generator()


def _traced_agent_run_live(wrapped, instance, args, kwargs):
    """Trace the main execution of a live agent (async generator)."""
    tracer, service = _get_tracer_and_service(instance)
    agen = wrapped(*args, **kwargs)

    async def _generator():
        with tracer.trace("google_adk.agent.run_live", service=service) as span:
            span.set_tag_str("component", "google-adk")
            span.set_tag_str("span.kind", "internal")
            span.set_tag_str("agent.name", getattr(instance, "name", ""))
            try:
                async for event in agen:
                    yield event
            except Exception as e:
                span.set_exc_info(type(e), e, e.__traceback__)
                raise

    return _generator()


async def _traced_functions_call_tool_async(wrapped, instance, args, kwargs):
    tracer, service = _get_tracer_and_service(adk)
    tool = args[0] if args else kwargs.get("tool")
    tool_args = args[1] if len(args) > 1 else kwargs.get("args")
    with tracer.trace("google_adk.tool.run", service=service) as span:
        span.set_tag_str("component", "google-adk")
        span.set_tag_str("span.kind", "internal")
        if tool is not None:
            span.set_tag_str("tool.name", getattr(tool, "name", tool.__class__.__name__))
        if tool_args is not None:
            span.set_tag_str("tool.parameters", _truncate_value(tool_args))
        try:
            result = await wrapped(*args, **kwargs)
            if result is not None:
                span.set_tag_str("tool.output", _truncate_value(result))
            return result
        except Exception as e:
            span.set_exc_info(type(e), e, e.__traceback__)
            raise


async def _traced_functions_call_tool_live(wrapped, instance, args, kwargs):
    tracer, service = _get_tracer_and_service(adk)
    tool = args[0] if args else kwargs.get("tool")
    function_args = args[1] if len(args) > 1 else kwargs.get("args")
    with tracer.trace("google_adk.tool.run_live", service=service) as span:
        span.set_tag_str("component", "google-adk")
        span.set_tag_str("span.kind", "internal")
        if tool is not None:
            span.set_tag_str("tool.name", getattr(tool, "name", tool.__class__.__name__))
        if function_args is not None:
            span.set_tag_str("tool.parameters", _truncate_value(function_args))
        agen = await wrapped(*args, **kwargs)
        async for item in agen:
            yield item


def _traced_code_executor_execute_code(wrapped, instance, args, kwargs):
    """Trace the execution of code by the agent (sync)."""
    tracer, service = _get_tracer_and_service(instance)
    with tracer.trace("google_adk.code.execute", service=service) as span:
        span.set_tag_str("component", "google-adk")
        span.set_tag_str("span.kind", "internal")

        # Signature: execute_code(self, invocation_context, code_execution_input)
        code_input = None
        if "code_execution_input" in kwargs:
            code_input = kwargs["code_execution_input"]
        elif len(args) >= 2:
            code_input = args[1]

        code_snippet = getattr(code_input, "code", None)
        if code_snippet:
            span.set_tag_str("code.snippet", _truncate_value(code_snippet))

        try:
            result = wrapped(*args, **kwargs)
            # Result is CodeExecutionResult
            stdout = getattr(result, "stdout", None)
            stderr = getattr(result, "stderr", None)
            if stdout:
                span.set_tag_str("code.stdout", _truncate_value(stdout))
            if stderr:
                span.set_tag_str("code.stderr", _truncate_value(stderr))
            return result
        except Exception as e:
            span.set_exc_info(type(e), e, e.__traceback__)
            raise


async def _traced_plugin_manager_run_callbacks(wrapped, instance, args, kwargs):
    tracer, service = _get_tracer_and_service(instance)
    callback_name = args[0] if args else kwargs.get("callback_name", "")
    with tracer.trace("google_adk.plugin.callback", service=service) as span:
        span.set_tag_str("component", "google-adk")
        span.set_tag_str("span.kind", "internal")
        span.set_tag_str("plugin.callback_name", str(callback_name))
        try:
            return await wrapped(*args, **kwargs)
        except Exception as e:
            span.set_exc_info(type(e), e, e.__traceback__)
            raise


async def _traced_memory_add_session_async(wrapped, instance, args, kwargs):
    tracer, service = _get_tracer_and_service(instance)
    with tracer.trace("google_adk.memory.add_session", service=service) as span:
        span.set_tag_str("component", "google-adk")
        span.set_tag_str("span.kind", "internal")
        span.set_tag_str("memory.impl", instance.__class__.__name__)
        try:
            return await wrapped(*args, **kwargs)
        except Exception as e:
            span.set_exc_info(type(e), e, e.__traceback__)
            raise


async def _traced_memory_search_async(wrapped, instance, args, kwargs):
    tracer, service = _get_tracer_and_service(instance)
    with tracer.trace("google_adk.memory.search", service=service) as span:
        span.set_tag_str("component", "google-adk")
        span.set_tag_str("span.kind", "internal")
        span.set_tag_str("memory.impl", instance.__class__.__name__)
        query = kwargs.get("query")
        if query is None and len(args) >= 3:
            query = args[2]
        if query is not None:
            span.set_tag_str("memory.query", _truncate_value(query))
        try:
            return await wrapped(*args, **kwargs)
        except Exception as e:
            span.set_exc_info(type(e), e, e.__traceback__)
            raise
