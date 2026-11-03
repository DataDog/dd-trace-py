from crewai import Crew
import pytest

from tests.contrib.crewai.utils import CREWAI_VERSION
from tests.contrib.crewai.utils import FailingTool
from tests.contrib.crewai.utils import MaxTool
from tests.contrib.crewai.utils import requires_akickoff
from tests.contrib.crewai.utils import sum_tool


# The crewai 1.x hierarchical recording delegates only the first task, so it has its own snapshot.
HIERARCHICAL_CREW_SNAPSHOT = "tests.contrib.crewai.test_crewai.test_hierarchical_crew" + (
    "_v1" if CREWAI_VERSION >= (1, 0, 0) else ""
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


@requires_akickoff
@pytest.mark.snapshot(token="tests.contrib.crewai.test_crewai.test_basic_crew")
async def test_basic_crew_akickoff(crewai, basic_crew, request_vcr):
    with request_vcr.use_cassette("test_basic_crew.yaml"):
        await basic_crew.akickoff(inputs={"topic": "AI"})


# akickoff runs tasks inline rather than in a worker thread, which only changes _dd.top_level on the task span.
@requires_akickoff
@pytest.mark.snapshot(token="tests.contrib.crewai.test_crewai.test_crew_with_tool", ignores=["metrics._dd.top_level"])
async def test_crew_with_tool_akickoff(crewai, tool_crew, request_vcr):
    with request_vcr.use_cassette("test_crew_with_tool.yaml"):
        await tool_crew.akickoff(inputs={"ages": [10, 12, 14, 16, 18]})


@requires_akickoff
@pytest.mark.snapshot(
    token="tests.contrib.crewai.test_crewai.test_crew_with_async_tasks", ignores=["metrics._dd.top_level"]
)
async def test_crew_with_async_tasks_akickoff(crewai, async_exec_crew, request_vcr):
    with request_vcr.use_cassette("test_crew_with_async_tasks.yaml"):
        await async_exec_crew.akickoff(inputs={"ages": [10, 12, 14, 16, 18]})


@requires_akickoff
@pytest.mark.snapshot(ignores=["meta.error.stack", "meta.error.message"])
async def test_crew_akickoff_error(crewai, failing_llm_crew, request_vcr):
    with request_vcr.use_cassette("test_crew_akickoff_error.yaml"):
        with pytest.raises(Exception):
            await failing_llm_crew.akickoff()


@pytest.mark.skipif(CREWAI_VERSION < (1, 6, 0), reason="requires crewai>=1.6")
@pytest.mark.snapshot
def test_crew_stream(crewai, streaming_crew, request_vcr):
    with request_vcr.use_cassette("test_crew_stream.yaml"):
        output = streaming_crew.kickoff()
        for _ in output:
            pass


@pytest.fixture
def basic_crew_checkpoint(basic_crew, monkeypatch):
    # Restoring calls kickoff again on the restored crew. Return the same crew so the run replays the basic recording;
    # on_events=[] keeps the restored run from writing checkpoints.
    monkeypatch.setattr(Crew, "from_checkpoint", classmethod(lambda cls, config: basic_crew))
    # CheckpointConfig only exists in crewai>=1.15.
    from crewai import CheckpointConfig

    yield CheckpointConfig(restore_from="unused", on_events=[])


@pytest.mark.skipif(CREWAI_VERSION < (1, 15, 0), reason="requires crewai>=1.15")
@pytest.mark.snapshot(token="tests.contrib.crewai.test_crewai.test_basic_crew")
def test_crew_kickoff_from_checkpoint(crewai, basic_crew, basic_crew_checkpoint, request_vcr):
    with request_vcr.use_cassette("test_basic_crew.yaml"):
        basic_crew.kickoff(inputs={"topic": "AI"}, from_checkpoint=basic_crew_checkpoint)


@pytest.mark.skipif(CREWAI_VERSION < (1, 15, 0), reason="requires crewai>=1.15")
@pytest.mark.snapshot(token="tests.contrib.crewai.test_crewai.test_basic_crew")
async def test_crew_akickoff_from_checkpoint(crewai, basic_crew, basic_crew_checkpoint, request_vcr):
    with request_vcr.use_cassette("test_basic_crew.yaml"):
        await basic_crew.akickoff(inputs={"topic": "AI"}, from_checkpoint=basic_crew_checkpoint)


@requires_akickoff
@pytest.mark.snapshot(token="tests.contrib.crewai.test_crewai.test_simple_flow")
async def test_simple_flow_akickoff(crewai, simple_flow_async):
    await simple_flow_async.akickoff(inputs={"continent": "North America"})


# Flow streaming exists since crewai 1.6, but kickoff_async returns an async-iterable session only since 1.15.10.
@pytest.mark.skipif(CREWAI_VERSION < (1, 15, 10), reason="requires crewai>=1.15.10")
@pytest.mark.snapshot(token="tests.contrib.crewai.test_crewai.test_simple_flow")
async def test_simple_flow_stream(crewai, simple_flow_async):
    simple_flow_async.stream = True
    session = await simple_flow_async.kickoff_async(inputs={"continent": "North America"})
    async for _ in session:
        pass


# Native function calling (crewai 1.x) calls Tool.run (decorated tools) and BaseTool.run (subclasses) directly.
@pytest.mark.snapshot
def test_tool_run(crewai):
    assert sum_tool.run(entries=[10, 12, 14]) == 36
    assert MaxTool().run(entries=[10, 12, 14]) == 14


@pytest.mark.snapshot
async def test_tool_ainvoke(crewai):
    assert await MaxTool().to_structured_tool().ainvoke(input={"entries": [10, 12, 14]}) == 14


@pytest.mark.parametrize("use_ainvoke", [False, True], ids=["run", "ainvoke"])
@pytest.mark.snapshot(token="tests.contrib.crewai.test_crewai.test_tool_error", ignores=["meta.error.stack"])
async def test_tool_error(crewai, use_ainvoke):
    with pytest.raises(ValueError):
        if use_ainvoke:
            await FailingTool().to_structured_tool().ainvoke(input={"entries": [10, 12, 14]})
        else:
            FailingTool().run(entries=[10, 12, 14])
