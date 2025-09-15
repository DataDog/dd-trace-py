import asyncio

import pytest

from ddtrace._trace.pin import Pin


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

    ctx = {}
    await adk.flows.llm_flows.functions.__call_tool_async(tool, {"a": 1}, ctx)

    span = mock_tracer.pop_traces()[0][0]
    assert span.name == "google_adk.tool.run"
    assert span.get_tag("component") == "google-adk"
    assert span.get_tag("span.kind") == "internal"
    assert span.get_tag("tool.name") is not None
    assert span.get_tag("tool.parameters") is not None
    assert "a" in span.get_tag("tool.parameters")
    assert span.get_tag("tool.output") is not None
    assert "ok" in span.get_tag("tool.output")


def test_traced_code_executor_execute_code_creates_span(mock_tracer, adk, dummy_code_input, dummy_code_result):
    inst = adk.code_executors.UnsafeLocalCodeExecutor()
    Pin()._override(inst, tracer=mock_tracer)
    inst.execute_code(None, dummy_code_input)

    span = mock_tracer.pop_traces()[0][0]
    assert span.name == "google_adk.code.execute"
    assert span.get_tag("code.snippet").startswith("print(")
    assert span.get_tag("code.stdout") == "hello\n"
    assert span.get_tag("component") == "google-adk"
    assert span.get_tag("span.kind") == "internal"


def test_traced_code_executor_execute_code_stderr_tag(mock_tracer, adk):
    inst = adk.code_executors.UnsafeLocalCodeExecutor()
    Pin()._override(inst, tracer=mock_tracer)
    inst.execute_code(None, type("I", (), {"code": "x"})())

    span = mock_tracer.pop_traces()[0][0]
    assert span.name == "google_adk.code.execute"
    assert "name 'x' is not defined" in (span.get_tag("code.stderr") or "")


@pytest.mark.asyncio
async def test_traced_agent_run_async_creates_span(mock_tracer, adk, dummy_runner):
    wrapped = adk.runners.Runner.run_async
    orig_impl = getattr(wrapped, "__wrapped__", None)

    async def fake_run_async(self, *args, **kwargs):
        async def agen():
            yield object()

        return agen()

    try:
        wrapped.__wrapped__ = fake_run_async
        agen = adk.runners.Runner.run_async(dummy_runner, user_id="u", session_id="s", new_message=object())
        async for _ in agen:
            pass
    finally:
        if orig_impl is not None:
            wrapped.__wrapped__ = orig_impl

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
    m = adk.memory.InMemoryMemoryService()
    Pin()._override(m, tracer=mock_tracer)

    add_wrapped = adk.memory.InMemoryMemoryService.add_session_to_memory
    search_wrapped = adk.memory.InMemoryMemoryService.search_memory
    orig_add_impl = getattr(add_wrapped, "__wrapped__", None)
    orig_search_impl = getattr(search_wrapped, "__wrapped__", None)

    async def fake_add_session_to_memory(self, *args, **kwargs):
        return True

    async def fake_search_memory(self, *args, **kwargs):
        return []

    try:
        add_wrapped.__wrapped__ = fake_add_session_to_memory
        search_wrapped.__wrapped__ = fake_search_memory
        await m.add_session_to_memory(type("S", (), {"app_name": "app", "user_id": "user"})())
        await m.search_memory(query="hello")
    finally:
        if orig_add_impl is not None:
            add_wrapped.__wrapped__ = orig_add_impl
        if orig_search_impl is not None:
            search_wrapped.__wrapped__ = orig_search_impl

    traces = mock_tracer.pop_traces()
    names = [s.name for s in traces[0]]
    assert "google_adk.memory.add_session" in names
    assert "google_adk.memory.search" in names
    add_span = next(s for s in traces[0] if s.name == "google_adk.memory.add_session")
    search_span = next(s for s in traces[0] if s.name == "google_adk.memory.search")
    assert add_span.get_tag("memory.impl") == "InMemoryMemoryService"
    assert add_span.get_tag("component") == "google-adk"
    assert add_span.get_tag("span.kind") == "internal"
    assert search_span.get_tag("memory.impl") == "InMemoryMemoryService"
    assert search_span.get_tag("memory.query") == "hello"
    assert search_span.get_tag("component") == "google-adk"
    assert search_span.get_tag("span.kind") == "internal"


@pytest.mark.asyncio
async def test_traced_plugin_callback_creates_span(mock_tracer, adk):
    pm = adk.plugins.plugin_manager.PluginManager()
    Pin()._override(pm, tracer=mock_tracer)

    wrapped = adk.plugins.plugin_manager.PluginManager._run_callbacks
    orig_impl = getattr(wrapped, "__wrapped__", None)

    async def fake_run_callbacks(self, *args, **kwargs):
        return True

    try:
        wrapped.__wrapped__ = fake_run_callbacks
        await pm._run_callbacks("on_event")
    finally:
        if orig_impl is not None:
            wrapped.__wrapped__ = orig_impl

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

    agen = adk.flows.llm_flows.functions.__call_tool_live(tool, {"x": 2}, {}, {})
    async for _ in agen:
        pass

    span = mock_tracer.pop_traces()[0][0]
    assert span.name == "google_adk.tool.run_live"
    assert span.get_tag("component") == "google-adk"
    assert span.get_tag("span.kind") == "internal"
    assert span.get_tag("tool.name") is not None
    assert "x" in span.get_tag("tool.parameters")


@pytest.mark.asyncio
async def test_traced_agent_run_live_creates_span(mock_tracer, adk):
    wrapped = adk.runners.Runner.run_live
    orig_impl = getattr(wrapped, "__wrapped__", None)

    async def fake_run_live(self, *args, **kwargs):
        async def agen():
            yield 1

        return agen()

    try:
        wrapped.__wrapped__ = fake_run_live
        agen = adk.runners.Runner.run_live(type("R", (), {"name": "runner-live"})(), live_request_queue=object())
        async for _ in agen:
            pass
    finally:
        if orig_impl is not None:
            wrapped.__wrapped__ = orig_impl

    span = mock_tracer.pop_traces()[0][0]
    assert span.name == "google_adk.agent.run_live"
    assert span.get_tag("component") == "google-adk"
    assert span.get_tag("span.kind") == "internal"
    assert span.get_tag("agent.name") == "runner-live"


@pytest.mark.asyncio
async def test_error_tags_set_on_exception(mock_tracer, adk):
    wrapped = adk.flows.llm_flows.functions.__call_tool_async
    orig_impl = getattr(wrapped, "__wrapped__", None)

    class Boom(Exception):
        pass

    async def raising_impl(tool, args):
        raise Boom("kaboom")

    try:
        wrapped.__wrapped__ = raising_impl
        FunctionTool = adk.tools.function_tool.FunctionTool

        async def noop():
            return None

        tool = FunctionTool(func=noop)
        with pytest.raises(Boom):
            await adk.flows.llm_flows.functions.__call_tool_async(tool, {})
    finally:
        if orig_impl is not None:
            wrapped.__wrapped__ = orig_impl

    span = mock_tracer.pop_traces()[0][0]
    assert span.error == 1
    assert span.get_tag("error.type") is not None
    assert span.get_tag("error.message") is not None
    assert span.get_tag("error.stack") is not None
