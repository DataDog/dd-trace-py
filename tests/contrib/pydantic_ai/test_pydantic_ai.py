async def test_agent_run(pydantic_ai, snapshot_context, request_vcr):
    with snapshot_context(token="tests.contrib.pydantic_ai.test_pydantic_ai.test_agent_run"):
        with request_vcr.use_cassette("agent_run.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o")
            await agent.run("Hello, world!")



def test_agent_run_sync(pydantic_ai, snapshot_context, request_vcr):
    with snapshot_context(token="tests.contrib.pydantic_ai.test_pydantic_ai.test_agent_run_sync"):
        with request_vcr.use_cassette("agent_run_sync.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o")
            agent.run_sync("Hello, world!")
