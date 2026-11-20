import pytest


@pytest.mark.snapshot
def test_basic_crew(crewai, basic_crew, request_vcr):
    with request_vcr.use_cassette("test_basic_crew.yaml"):
        basic_crew.kickoff(inputs={"topic": "AI"})


@pytest.mark.snapshot(token="tests.contrib.crewai.test_crewai.test_basic_crew")
def test_basic_crew_for_each(crewai, basic_crew, request_vcr):
    with request_vcr.use_cassette("test_basic_crew.yaml"):
        basic_crew.kickoff_for_each(inputs=[{"topic": "AI"}])


@pytest.mark.snapshot(token="tests.contrib.crewai.test_crewai.test_basic_crew")
async def test_basic_crew_async(crewai, basic_crew, request_vcr):
    with request_vcr.use_cassette("test_basic_crew.yaml"):
        await basic_crew.kickoff_async(inputs={"topic": "AI"})


@pytest.mark.snapshot(token="tests.contrib.crewai.test_crewai.test_basic_crew")
async def test_basic_crew_async_for_each(crewai, basic_crew, request_vcr):
    with request_vcr.use_cassette("test_basic_crew.yaml"):
        await basic_crew.kickoff_for_each_async(inputs=[{"topic": "AI"}])


@pytest.mark.snapshot
def test_crew_with_tool(crewai, tool_crew, request_vcr):
    with request_vcr.use_cassette("test_crew_with_tool.yaml"):
        tool_crew.kickoff(inputs={"ages": [10, 12, 14, 16, 18]})


@pytest.mark.snapshot(token="tests.contrib.crewai.test_crewai.test_crew_with_tool")
def test_crew_with_tool_for_each(crewai, tool_crew, request_vcr):
    with request_vcr.use_cassette("test_crew_with_tool.yaml"):
        tool_crew.kickoff_for_each(inputs=[{"ages": [10, 12, 14, 16, 18]}])


@pytest.mark.snapshot(token="tests.contrib.crewai.test_crewai.test_crew_with_tool")
async def test_crew_with_tool_async(crewai, tool_crew, request_vcr):
    with request_vcr.use_cassette("test_crew_with_tool.yaml"):
        await tool_crew.kickoff_async(inputs={"ages": [10, 12, 14, 16, 18]})


@pytest.mark.snapshot(token="tests.contrib.crewai.test_crewai.test_crew_with_tool")
async def test_crew_with_tool_async_for_each(crewai, tool_crew, request_vcr):
    with request_vcr.use_cassette("test_crew_with_tool.yaml"):
        await tool_crew.kickoff_for_each_async(inputs=[{"ages": [10, 12, 14, 16, 18]}])


@pytest.mark.snapshot
def test_crew_with_async_tasks(crewai, async_exec_crew, request_vcr):
    with request_vcr.use_cassette("test_crew_with_async_tasks.yaml"):
        async_exec_crew.kickoff(inputs={"ages": [10, 12, 14, 16, 18]})


@pytest.mark.snapshot(token="tests.contrib.crewai.test_crewai.test_crew_with_async_tasks")
def test_crew_with_async_tasks_for_each(crewai, async_exec_crew, request_vcr):
    with request_vcr.use_cassette("test_crew_with_async_tasks.yaml"):
        async_exec_crew.kickoff_for_each(inputs=[{"ages": [10, 12, 14, 16, 18]}])


@pytest.mark.snapshot(token="tests.contrib.crewai.test_crewai.test_crew_with_async_tasks")
async def test_crew_with_async_tasks_async(crewai, async_exec_crew, request_vcr):
    with request_vcr.use_cassette("test_crew_with_async_tasks.yaml"):
        await async_exec_crew.kickoff_async(inputs={"ages": [10, 12, 14, 16, 18]})


@pytest.mark.snapshot(token="tests.contrib.crewai.test_crewai.test_crew_with_async_tasks")
async def test_crew_with_async_tasks_async_for_each(crewai, async_exec_crew, request_vcr):
    with request_vcr.use_cassette("test_crew_with_async_tasks.yaml"):
        await async_exec_crew.kickoff_for_each_async(inputs=[{"ages": [10, 12, 14, 16, 18]}])


@pytest.mark.snapshot
def test_conditional_crew(crewai, conditional_crew, request_vcr):
    with request_vcr.use_cassette("test_crew_with_async_tasks.yaml"):
        conditional_crew.kickoff(inputs={"ages": [10, 12, 14, 16, 18]})


@pytest.mark.snapshot(token="tests.contrib.crewai.test_crewai.test_conditional_crew")
async def test_conditional_crew_async(crewai, conditional_crew, request_vcr):
    with request_vcr.use_cassette("test_crew_with_async_tasks.yaml"):
        await conditional_crew.kickoff_async(inputs={"ages": [10, 12, 14, 16, 18]})


@pytest.mark.snapshot
def test_hierarchical_crew(crewai, hierarchical_crew, request_vcr):
    with request_vcr.use_cassette("test_hierarchical_crew.yaml"):
        hierarchical_crew.kickoff(inputs={"ages": [10, 12, 14, 16, 18]})


@pytest.mark.snapshot(token="tests.contrib.crewai.test_crewai.test_hierarchical_crew")
def test_hierarchical_crew_for_each(crewai, hierarchical_crew, request_vcr):
    with request_vcr.use_cassette("test_hierarchical_crew.yaml"):
        hierarchical_crew.kickoff_for_each(inputs=[{"ages": [10, 12, 14, 16, 18]}])


@pytest.mark.snapshot(token="tests.contrib.crewai.test_crewai.test_hierarchical_crew")
async def test_hierarchical_crew_async(crewai, hierarchical_crew, request_vcr):
    with request_vcr.use_cassette("test_hierarchical_crew.yaml"):
        await hierarchical_crew.kickoff_async(inputs={"ages": [10, 12, 14, 16, 18]})


@pytest.mark.snapshot(token="tests.contrib.crewai.test_crewai.test_hierarchical_crew")
async def test_hierarchical_crew_async_for_each(crewai, hierarchical_crew, request_vcr):
    with request_vcr.use_cassette("test_hierarchical_crew.yaml"):
        await hierarchical_crew.kickoff_for_each_async(inputs=[{"ages": [10, 12, 14, 16, 18]}])


@pytest.mark.snapshot(token="tests.contrib.crewai.test_crewai.test_simple_flow")
def test_simple_flow(crewai, simple_flow):
    simple_flow.kickoff(inputs={"continent": "North America"})


@pytest.mark.snapshot(token="tests.contrib.crewai.test_crewai.test_simple_flow")
async def test_simple_flow_async(crewai, simple_flow_async):
    await simple_flow_async.kickoff_async(inputs={"continent": "North America"})


@pytest.mark.snapshot(token="tests.contrib.crewai.test_crewai.test_complex_flow")
def test_complex_flow(crewai, complex_flow):
    complex_flow.kickoff(inputs={"continent": "North America"})


@pytest.mark.snapshot(token="tests.contrib.crewai.test_crewai.test_complex_flow")
async def test_complex_flow_async(crewai, complex_flow_async):
    await complex_flow_async.kickoff_async(inputs={"continent": "North America"})


async def test_flow_kickoff_cancellation_is_recorded(crewai, test_spans):
    import asyncio

    from ddtrace.contrib.internal.crewai.patch import traced_flow_kickoff

    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await traced_flow_kickoff(cancelled, object(), (), {})

    (span,) = [span for trace in test_spans.pop_traces() for span in trace]
    assert span.error == 1
    assert span.get_tag("error.type") == "asyncio.exceptions.CancelledError"


async def test_flow_method_cancellation_is_recorded(crewai, test_spans):
    import asyncio
    from types import SimpleNamespace

    from ddtrace.contrib.internal.crewai.patch import traced_flow_method

    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await traced_flow_method(cancelled, SimpleNamespace(state={}), ("method",), {})

    (span,) = [span for trace in test_spans.pop_traces() for span in trace]
    assert span.error == 1
    assert span.get_tag("error.type") == "asyncio.exceptions.CancelledError"


async def test_flow_method_span_finishes_when_state_capture_raises(crewai, tracer, test_spans):
    from types import SimpleNamespace

    from ddtrace.contrib.internal.crewai.patch import traced_flow_method

    class BrokenState:
        def model_dump(self):
            raise ValueError("cannot serialize state")

    async def method(*args, **kwargs):
        raise AssertionError("the method must not run when its state cannot be captured")

    with pytest.raises(ValueError):
        await traced_flow_method(method, SimpleNamespace(state=BrokenState()), ("method",), {})

    assert tracer.current_span() is None
    (span,) = [span for trace in test_spans.pop_traces() for span in trace]
    assert span.error == 1
    assert span.get_tag("error.type") == "builtins.ValueError"
