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
