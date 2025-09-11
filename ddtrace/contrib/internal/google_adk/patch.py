
"""Instrument google-adk."""
import google.adk

from ddtrace._trace.pin import Pin
from ddtrace import config
from ddtrace import tracer as _tracer
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace._trace._limits import TRUNCATED_SPAN_ATTRIBUTE_LEN


# The service name can be configured by the user.
# config.google_adk["service_name"]
# DEV: a-la google cloud integration, we could have a project and other settings.
# config.google_adk["project"]

config._add(
    "google_adk",
    dict(
        service_name="google-adk",
    ),
)


def get_version():
    """Get the version of the google.adk library."""
    return ""


def _truncate_value(value):
    try:
        s = str(value)
    except Exception:
        s = repr(value)
    if len(s) > TRUNCATED_SPAN_ATTRIBUTE_LEN:
        return s[:TRUNCATED_SPAN_ATTRIBUTE_LEN]
    return s


def patch():
    """Patch the `google.adk` library for tracing."""

    # Agent entrypoints (async generators)
    # wrap("google.adk", "agents.base_agent.BaseAgent.run_async", _traced_agent_run_async)
    # wrap("google.adk", "agents.base_agent.BaseAgent.run_live", _traced_agent_run_live)
    wrap("google.adk", "runners.Runner.run_async", _traced_agent_run_async)
    wrap("google.adk", "runners.Runner.run_live", _traced_agent_run_live)

    # Tool execution (async)
    wrap("google.adk", "tools.base_tool.BaseTool.run_async", _traced_tool_run_async)

    # Code execution (sync)

    code_executors = {
        "built_in_code_executor": "BuiltInCodeExecutor",
        "vertex_ai_code_executor": "VertexAiCodeExecutor",
        "unsafe_local_code_executor": "UnsafeLocalCodeExecutor",
        "container_code_executor": "ContainerCodeExecutor",
    }
    code_executor_classes = [
        # "BuiltInCodeExecutor", # make an llm tool call to use the llms built in code executor
        "VertexAiCodeExecutor",
        "UnsafeLocalCodeExecutor",
        # "ContainerCodeExecutor", # additional package dependendy
    ]

    for code_executor in code_executor_classes:
        wrap(
            "google.adk", f"code_executors.{code_executor}.execute_code",
            _traced_code_executor_execute_code,
        )

    # Plugin callbacks orchestration
    wrap(
        "google.adk", "plugins.plugin_manager.PluginManager._run_callbacks",
        _traced_plugin_manager_run_callbacks,
    )

    # Memory services (optional modules)
    wrap(
        "google.adk", "memory.in_memory_memory_service.InMemoryMemoryService.add_session_to_memory",
        _traced_memory_add_session_async,
    )
    wrap(
        "google.adk", "memory.in_memory_memory_service.InMemoryMemoryService.search_memory",   
        _traced_memory_search_async,
    )
    wrap(
        "google.adk", "memory.vertex_ai_memory_bank_service.VertexAiMemoryBankService.add_session_to_memory",
        _traced_memory_add_session_async,
    )
    wrap(
        "google.adk", "memory.vertex_ai_memory_bank_service.VertexAiMemoryBankService.search_memory",
        _traced_memory_search_async,
    )


def unpatch():
    """Unpatch the `google.adk` library."""
    try:
        import google.adk
        import google.adk.agents.base_agent
        import google.adk.code_executors.base_code_executor
        import google.adk.tools.base_tool
        import google.adk.plugins.plugin_manager
        import google.adk.memory.in_memory_memory_service
        import google.adk.memory.vertex_ai_memory_bank_service
    except ImportError:
        return

    if not hasattr(google.adk, "_datadog_patch") or not getattr(google.adk, "_datadog_patch"):
        return
    setattr(google.adk, "_datadog_patch", False)

    unwrap(google.adk, "agents.base_agent.BaseAgent.run_async")
    unwrap(google.adk, "agents.base_agent.BaseAgent.run_live")

    unwrap(google.adk, "tools.base_tool.BaseTool.run_async")

    unwrap(google.adk, "code_executors.base_code_executor.BaseCodeExecutor.execute_code")

    unwrap(google.adk, "plugins.plugin_manager.PluginManager._run_callbacks")

    # Memory services
    try:
        unwrap(
            google.adk, "memory.in_memory_memory_service.InMemoryMemoryService.add_session_to_memory",
        )
        unwrap(
            google.adk, "memory.in_memory_memory_service.InMemoryMemoryService.search_memory",
        )
    except Exception:
        pass
    try:
        unwrap(
            google.adk, "memory.vertex_ai_memory_bank_service.VertexAiMemoryBankService.add_session_to_memory",
        )
        unwrap(
            google.adk, "memory.vertex_ai_memory_bank_service.VertexAiMemoryBankService.search_memory",
        )
    except Exception:
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

    async def _generator():
        breakpoint()
        with tracer.trace("google_adk.agent.run", service=service) as span:
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


async def _traced_tool_run_async(wrapped, instance, args, kwargs):
    """Trace the execution of an agent tool (async)."""
    tracer, service = _get_tracer_and_service(instance)
    with tracer.trace("google_adk.tool.run", service=service) as span:
        span.set_tag_str("component", "google-adk")
        span.set_tag_str("span.kind", "internal")
        span.set_tag_str("tool.name", getattr(instance, "name", instance.__class__.__name__))

        tool_args = kwargs.get("args")
        if tool_args is None and args:
            # run_async(self, *, args: dict, tool_context: ToolContext)
            # enforce kw-only in user code, but be defensive here
            tool_args = args[0] if isinstance(args[0], dict) else None
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


def _traced_code_executor_execute_code(wrapped, instance, args, kwargs):
    """Trace the execution of code by the agent (sync)."""
    tracer, service = _get_tracer_and_service(instance)
    breakpoint()
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
    breakpoint()
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
