async def test_agent_run(pydantic_ai, snapshot_context, request_vcr):
    with snapshot_context(token="tests.contrib.pydantic_ai.test_pydantic_ai.test_agent_run"):
        with request_vcr.use_cassette("agent_run.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            await agent.run("Hello, world!")



def test_agent_run_sync(pydantic_ai, snapshot_context, request_vcr):
    with snapshot_context(token="tests.contrib.pydantic_ai.test_pydantic_ai.test_agent_run"):
        with request_vcr.use_cassette("agent_run_sync.yaml"):
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
