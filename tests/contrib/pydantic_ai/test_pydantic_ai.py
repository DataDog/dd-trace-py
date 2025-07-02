import pytest


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

            def calculate_square_tool(x: int) -> int:
                return x * x

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
