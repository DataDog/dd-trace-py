import crewai as crewai_module
from crewai import Crew
from crewai import Flow
from crewai.tools import BaseTool
from crewai.tools import tool
import pytest

from ddtrace.internal.utils.version import parse_version


# Crew.akickoff runs tasks, agents and tools with native async methods instead of a worker thread.
requires_native_async = pytest.mark.skipif(
    not hasattr(Crew, "akickoff"), reason="Crew.akickoff is not available in this crewai version"
)

# crewai>=1.0 replays a separate hierarchical recording whose manager delegates only the first task.
HIERARCHICAL_CREW_SNAPSHOT = "tests.contrib.crewai.test_crewai.test_hierarchical_crew" + (
    "_v1" if parse_version(getattr(crewai_module, "__version__", "0.0.0")) >= (1, 0, 0) else ""
)


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


@pytest.mark.snapshot(token=HIERARCHICAL_CREW_SNAPSHOT)
def test_hierarchical_crew(crewai, hierarchical_crew, request_vcr):
    with request_vcr.use_cassette("test_hierarchical_crew.yaml"):
        hierarchical_crew.kickoff(inputs={"ages": [10, 12, 14, 16, 18]})


@pytest.mark.snapshot(token=HIERARCHICAL_CREW_SNAPSHOT)
def test_hierarchical_crew_for_each(crewai, hierarchical_crew, request_vcr):
    with request_vcr.use_cassette("test_hierarchical_crew.yaml"):
        hierarchical_crew.kickoff_for_each(inputs=[{"ages": [10, 12, 14, 16, 18]}])


@pytest.mark.snapshot(token=HIERARCHICAL_CREW_SNAPSHOT)
async def test_hierarchical_crew_async(crewai, hierarchical_crew, request_vcr):
    with request_vcr.use_cassette("test_hierarchical_crew.yaml"):
        await hierarchical_crew.kickoff_async(inputs={"ages": [10, 12, 14, 16, 18]})


@pytest.mark.snapshot(token=HIERARCHICAL_CREW_SNAPSHOT)
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


@requires_native_async
@pytest.mark.snapshot(token="tests.contrib.crewai.test_crewai.test_basic_crew")
async def test_basic_crew_akickoff(crewai, basic_crew, request_vcr):
    with request_vcr.use_cassette("test_basic_crew.yaml"):
        await basic_crew.akickoff(inputs={"topic": "AI"})


@requires_native_async
# Tasks run inline under akickoff rather than in a worker thread, so _dd.top_level differs from kickoff.
@pytest.mark.snapshot(token="tests.contrib.crewai.test_crewai.test_crew_with_tool", ignores=["metrics._dd.top_level"])
async def test_crew_with_tool_akickoff(crewai, tool_crew, request_vcr):
    with request_vcr.use_cassette("test_crew_with_tool.yaml"):
        await tool_crew.akickoff(inputs={"ages": [10, 12, 14, 16, 18]})


@requires_native_async
# Tasks run inline under akickoff rather than in a worker thread, so _dd.top_level differs from kickoff.
@pytest.mark.snapshot(
    token="tests.contrib.crewai.test_crewai.test_crew_with_async_tasks", ignores=["metrics._dd.top_level"]
)
async def test_crew_with_async_tasks_akickoff(crewai, async_exec_crew, request_vcr):
    with request_vcr.use_cassette("test_crew_with_async_tasks.yaml"):
        await async_exec_crew.akickoff(inputs={"ages": [10, 12, 14, 16, 18]})


@pytest.mark.skipif(not hasattr(Flow, "akickoff"), reason="Flow.akickoff is not available")
@pytest.mark.snapshot(token="tests.contrib.crewai.test_crewai.test_simple_flow")
async def test_simple_flow_akickoff(crewai, simple_flow_async):
    await simple_flow_async.akickoff(inputs={"continent": "North America"})


@tool("Sum Calculator")
def sum_tool(entries: list) -> float:
    """This tool returns the sum of a list of numbers."""
    return sum(entries)


class MaxTool(BaseTool):
    name: str = "Max Calculator"
    description: str = "This tool returns the largest of a list of numbers."

    def _run(self, entries: list) -> float:
        return max(entries)


class FailingTool(BaseTool):
    name: str = "Failing Calculator"
    description: str = "This tool always fails."

    def _run(self, entries: list) -> float:
        raise ValueError("calculation failed")


# crewai>=1.0 executes native (function calling) tool calls through Tool.run / BaseTool.run.
@pytest.mark.snapshot
def test_tool_run(crewai):
    assert sum_tool.run(entries=[10, 12, 14]) == 36
    assert MaxTool().run(entries=[10, 12, 14]) == 14


@pytest.mark.snapshot(ignores=["meta.error.stack"])
def test_tool_run_error(crewai):
    with pytest.raises(ValueError):
        FailingTool().run(entries=[10, 12, 14])


@pytest.mark.snapshot
async def test_tool_ainvoke(crewai):
    assert await MaxTool().to_structured_tool().ainvoke(input={"entries": [10, 12, 14]}) == 14


@pytest.mark.snapshot(token="tests.contrib.crewai.test_crewai.test_tool_run_error", ignores=["meta.error.stack"])
async def test_tool_ainvoke_error(crewai):
    with pytest.raises(ValueError):
        await FailingTool().to_structured_tool().ainvoke(input={"entries": [10, 12, 14]})


@pytest.mark.skipif(not hasattr(Flow, "astream"), reason="Flow streaming is not available")
@pytest.mark.snapshot(token="tests.contrib.crewai.test_crewai.test_simple_flow")
async def test_simple_flow_stream(crewai, simple_flow_async):
    simple_flow_async.stream = True
    session = await simple_flow_async.kickoff_async(inputs={"continent": "North America"})
    async for _ in session:
        pass
