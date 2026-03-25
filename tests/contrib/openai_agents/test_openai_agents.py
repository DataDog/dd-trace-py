import pytest


@pytest.mark.snapshot
async def test_openai_agents(agents, request_vcr, simple_agent):
    """Test tracing with a simple agent with no tools or handoffs"""
    with request_vcr.use_cassette("test_simple_agent.yaml"):
        await agents.Runner.run(simple_agent, "What is the capital of France?")


@pytest.mark.snapshot
def test_openai_agents_sync(agents, request_vcr, simple_agent):
    """Test tracing with a simple agent with no tools or handoffs"""
    with request_vcr.use_cassette("test_simple_agent.yaml"):
        agents.Runner.run_sync(simple_agent, "What is the capital of France?")


@pytest.mark.snapshot
async def test_openai_agents_streaming(agents, request_vcr, simple_agent):
    from openai.types.responses import ResponseTextDeltaEvent

    """Test tracing with a simple agent with no tools or handoffs"""
    with request_vcr.use_cassette("test_simple_agent_streamed.yaml"):
        result = agents.Runner.run_streamed(simple_agent, "What is the capital of France?")
        async for event in result.stream_events():
            if event.type == "raw_response_event" and isinstance(event.data, ResponseTextDeltaEvent):
                pass


@pytest.mark.snapshot
async def test_openai_agents_with_tool_error(agents, request_vcr, addition_agent_with_tool_errors):
    with request_vcr.use_cassette("test_agent_with_tool_errors.yaml"):
        await agents.Runner.run(addition_agent_with_tool_errors, "What is the sum of 1 and 2?")
