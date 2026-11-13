import pytest

from tests.contrib.pydantic_ai.utils import calculate_square_tool


async def test_agent_run(pydantic_ai, snapshot_context, request_vcr):
    with snapshot_context(token="tests.contrib.pydantic_ai.test_pydantic_ai.test_agent_run"):
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            await agent.run("Hello, world!")


def test_agent_run_sync(pydantic_ai, snapshot_context, request_vcr):
    with snapshot_context(token="tests.contrib.pydantic_ai.test_pydantic_ai.test_agent_run"):
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            agent.run_sync("Hello, world!")


async def test_agent_run_stream(pydantic_ai, snapshot_context, request_vcr):
    with snapshot_context(token="tests.contrib.pydantic_ai.test_pydantic_ai.test_agent_run"):
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.run_stream("Hello, world!") as result:
                await result.get_output()


async def test_agent_iter(pydantic_ai, snapshot_context, request_vcr):
    with snapshot_context(token="tests.contrib.pydantic_ai.test_pydantic_ai.test_agent_run"):
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.iter("Hello, world!") as agent_run:
                async for _ in agent_run:
                    pass


async def test_agent_iter_error(pydantic_ai, snapshot_context, request_vcr):
    with snapshot_context(
        token="tests.contrib.pydantic_ai.test_pydantic_ai.test_agent_run_error", ignores=["meta.error.stack"]
    ):
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            with pytest.raises(Exception, match="test error"):
                async with agent.iter("Hello, world!") as agent_run:
                    async for _ in agent_run:
                        raise Exception("test error")


def test_agent_with_tool(pydantic_ai, snapshot_context, request_vcr):
    instructions = "Use the provided tool to calculate the square of 2."
    with snapshot_context(token="tests.contrib.pydantic_ai.test_pydantic_ai.test_agent_with_tools"):
        with request_vcr.use_cassette("agent_with_tools.yaml"):
            agent = pydantic_ai.Agent(
                model="gpt-4o", name="test_agent", tools=[calculate_square_tool], instructions=instructions
            )
            agent.run_sync("What is the square of 2?")


def test_agent_with_tool_decorator(pydantic_ai, snapshot_context, request_vcr):
    instructions = "Use the provided tool to calculate the square of 2."
    with snapshot_context(token="tests.contrib.pydantic_ai.test_pydantic_ai.test_agent_with_tools"):
        with request_vcr.use_cassette("agent_with_tools.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", instructions=instructions)

            @agent.tool_plain
            def calculate_square_tool(x: int) -> int:
                return x * x

            agent.run_sync("What is the square of 2?")


def _integration_container_sizes(integration):
    attrs = {**vars(type(integration)), **vars(integration)}
    return {
        name: len(value)
        for name, value in attrs.items()
        if not name.startswith("__") and isinstance(value, (dict, list, set))
    }


async def test_agent_runs_do_not_grow_integration_state(pydantic_ai, request_vcr):
    """Per-run bookkeeping must not accumulate on the integration for the life of the process."""
    integration = pydantic_ai._datadog_integration
    before = _integration_container_sizes(integration)
    instructions = "Use the provided tool to calculate the square of 2."
    agent = pydantic_ai.Agent(
        model="gpt-4o", name="test_agent", tools=[calculate_square_tool], instructions=instructions
    )
    for _ in range(3):
        with request_vcr.use_cassette("agent_with_tools.yaml"):
            await agent.run("What is the square of 2?")
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            async with pydantic_ai.Agent(model="gpt-4o", name="test_agent").run_stream("Hello, world!") as result:
                await result.get_output()
        with request_vcr.use_cassette("agent_iter.yaml"):
            with pytest.raises(Exception, match="test error"):
                async with pydantic_ai.Agent(model="gpt-4o", name="test_agent").iter("Hello, world!") as agent_run:
                    async for _ in agent_run:
                        raise Exception("test error")
    assert _integration_container_sizes(integration) == before
