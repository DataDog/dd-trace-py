import asyncio

import pytest

from ddtrace._trace.pin import Pin
from ddtrace.contrib.internal.google_adk import patch as adk_patch


@pytest.mark.asyncio
async def test_traced_functions_call_tool_async_creates_span(mock_tracer, adk):
    # Use ADK's function caller directly with a simple FunctionTool to avoid external calls
    tool_fn_called = False

    async def tool_fn(a: int):
        nonlocal tool_fn_called
        tool_fn_called = True
        return {"ok": True, "a": a}

    FunctionTool = adk.tools.function_tool.FunctionTool
    tool = FunctionTool(func=tool_fn)

    await adk.flows.llm_flows.functions.__call_tool_async(tool, {"a": 1})

    span = mock_tracer.pop_traces()[0][0]
    assert span.name == "google_adk.tool.run"
    assert span.get_tag("component") == "google-adk"
    assert span.get_tag("span.kind") == "internal"
    assert span.get_tag("tool.name") == "t"
    assert span.get_tag("tool.parameters") is not None
    assert "a" in span.get_tag("tool.parameters")
    assert span.get_tag("tool.output") is not None
    assert "ok" in span.get_tag("tool.output")


def test_traced_code_executor_execute_code_creates_span(mock_tracer, dummy_code_input, dummy_code_result):
    class Exec:
        pass

    instance = Exec()
    Pin()._override(instance, tracer=mock_tracer)

    def wrapped(invocation_context, code_execution_input):
        return dummy_code_result

    adk_patch._traced_code_executor_execute_code(wrapped, instance, (None, dummy_code_input), {})

    span = mock_tracer.pop_traces()[0][0]
    assert span.name == "google_adk.code.execute"
    assert span.get_tag("code.snippet").startswith("print(")
    assert span.get_tag("code.stdout") == "ok"
    assert span.get_tag("component") == "google-adk"
    assert span.get_tag("span.kind") == "internal"


def test_traced_code_executor_execute_code_stderr_tag(mock_tracer):
    class Exec:
        pass

    instance = Exec()
    Pin()._override(instance, tracer=mock_tracer)

    def wrapped(invocation_context, code_execution_input):
        class Res:
            stdout = ""
            stderr = "oh no"

        return Res()

    adk_patch._traced_code_executor_execute_code(wrapped, instance, (None, type("I", (), {"code": "x"})()), {})

    span = mock_tracer.pop_traces()[0][0]
    assert span.name == "google_adk.code.execute"
    assert span.get_tag("code.stderr") == "oh no"


@pytest.mark.asyncio
async def test_traced_agent_run_async_creates_span(mock_tracer, adk, dummy_runner):
    # Re-patch around a fake implementation to ensure wrapper wraps the fake
    orig = adk.runners.Runner.run_async
    try:
        adk_patch.unpatch()

        async def fake_run_async(self, *args, **kwargs):
            async def agen():
                yield object()

            return agen()

        adk.runners.Runner.run_async = fake_run_async
        adk_patch.patch()

        agen = await adk.runners.Runner.run_async(dummy_runner, user_id="u", session_id="s")
        async for _ in agen:
            pass
    finally:
        adk_patch.unpatch()
        adk.runners.Runner.run_async = orig
        adk_patch.patch()

    span = mock_tracer.pop_traces()[0][0]
    assert span.name == "google_adk.agent.run"
    assert span.get_tag("agent_name") == "test-agent"
    assert span.get_tag("tool.[0].name") == "get_current_weather"
    assert span.get_tag("tool.[0].description") == "weather"
    assert span.get_tag("tool.[0].parameters.[0]") == "location"
    assert span.get_tag("agent_instructions") == "do things"
    assert span.get_tag("model_configuration") is not None
    assert span.get_tag("session_management.session_id") == "s"
    assert span.get_tag("session_management.user_id") == "u"
    assert span.get_tag("component") == "google-adk"
    assert span.get_tag("span.kind") == "internal"


@pytest.mark.asyncio
async def test_traced_memory_methods_create_spans(mock_tracer, adk):
    # Call ADK memory service methods directly
    m = adk.memory.InMemoryMemoryService()
    Pin()._override(m, tracer=mock_tracer)

    async def fake_add_session_to_memory(self, *args, **kwargs):
        return True

    async def fake_search_memory(self, *args, **kwargs):
        return []

    orig_add = adk.memory.InMemoryMemoryService.add_session_to_memory
    orig_search = adk.memory.InMemoryMemoryService.search_memory
    try:
        adk.memory.InMemoryMemoryService.add_session_to_memory = fake_add_session_to_memory
        adk.memory.InMemoryMemoryService.search_memory = fake_search_memory
        await m.add_session_to_memory("app", "user", "session")
        await m.search_memory("app", "user", "hello")
    finally:
        adk.memory.InMemoryMemoryService.add_session_to_memory = orig_add
        adk.memory.InMemoryMemoryService.search_memory = orig_search

    traces = mock_tracer.pop_traces()
    names = [s.name for s in traces[0]]
    assert "google_adk.memory.add_session" in names
    assert "google_adk.memory.search" in names
    add_span = next(s for s in traces[0] if s.name == "google_adk.memory.add_session")
    search_span = next(s for s in traces[0] if s.name == "google_adk.memory.search")
    assert add_span.get_tag("memory.impl") == "Mem"
    assert add_span.get_tag("component") == "google-adk"
    assert add_span.get_tag("span.kind") == "internal"
    assert search_span.get_tag("memory.impl") == "Mem"
    assert search_span.get_tag("memory.query") == "hello"
    assert search_span.get_tag("component") == "google-adk"
    assert search_span.get_tag("span.kind") == "internal"


@pytest.mark.asyncio
async def test_traced_plugin_callback_creates_span(mock_tracer, adk):
    pm = adk.plugins.plugin_manager.PluginManager()
    Pin()._override(pm, tracer=mock_tracer)

    # Call real method; with no plugins, it should be a quick no-op path
    await pm._run_callbacks("on_event")

    span = mock_tracer.pop_traces()[0][0]
    assert span.name == "google_adk.plugin.callback"
    assert span.get_tag("plugin.callback_name") == "on_event"
    assert span.get_tag("component") == "google-adk"
    assert span.get_tag("span.kind") == "internal"


@pytest.mark.asyncio
async def test_traced_functions_call_tool_live_creates_span(mock_tracer, adk):
    # Use FunctionTool with an async generator response to exercise live flow
    async def tool_fn(x: int):
        return {"x": x}

    FunctionTool = adk.tools.function_tool.FunctionTool
    tool = FunctionTool(func=tool_fn)

    agen = await adk.flows.llm_flows.functions.__call_tool_live(tool, {"x": 2})
    async for _ in agen:
        pass

    span = mock_tracer.pop_traces()[0][0]
    assert span.name == "google_adk.tool.run_live"
    assert span.get_tag("component") == "google-adk"
    assert span.get_tag("span.kind") == "internal"
    assert span.get_tag("tool.name") == "t-live"
    assert "x" in span.get_tag("tool.parameters")


@pytest.mark.asyncio
async def test_traced_agent_run_live_creates_span(mock_tracer, adk):
    # Re-patch around a fake implementation to ensure wrapper wraps the fake
    orig = adk.runners.Runner.run_live
    try:
        adk_patch.unpatch()

        async def fake_run_live(self, *args, **kwargs):
            async def agen():
                yield 1

            return agen()

        adk.runners.Runner.run_live = fake_run_live
        adk_patch.patch()

        agen = await adk.runners.Runner.run_live(type("R", (), {"name": "runner-live"})())
        async for _ in agen:
            pass
    finally:
        adk_patch.unpatch()
        adk.runners.Runner.run_live = orig
        adk_patch.patch()

    span = mock_tracer.pop_traces()[0][0]
    assert span.name == "google_adk.agent.run_live"
    assert span.get_tag("component") == "google-adk"
    assert span.get_tag("span.kind") == "internal"
    assert span.get_tag("agent.name") == "runner-live"


@pytest.mark.asyncio
async def test_error_tags_set_on_exception(mock_tracer, adk):
    # Re-patch around a raising implementation to ensure wrapper captures error tags
    from ddtrace.contrib.internal.google_adk import patch as patch_mod
    orig = adk.flows.llm_flows.functions.__call_tool_async
    try:
        patch_mod.unpatch()

        class Boom(Exception):
            pass

        async def raising_impl(tool, args):
            raise Boom("kaboom")

        adk.flows.llm_flows.functions.__call_tool_async = raising_impl
        patch_mod.patch()

        FunctionTool = adk.tools.function_tool.FunctionTool

        async def noop():
            return None

        tool = FunctionTool(func=noop)
        with pytest.raises(Boom):
            await adk.flows.llm_flows.functions.__call_tool_async(tool, {})
    finally:
        patch_mod.unpatch()
        adk.flows.llm_flows.functions.__call_tool_async = orig
        patch_mod.patch()

    span = mock_tracer.pop_traces()[0][0]
    assert span.error == 1
    assert span.get_tag("error.type") is not None
    assert span.get_tag("error.message") is not None
    assert span.get_tag("error.stack") is not None
