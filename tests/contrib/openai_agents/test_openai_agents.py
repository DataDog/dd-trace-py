import pytest


@pytest.mark.snapshot
async def test_openai_agents(agents, request_vcr, simple_agent):
    """Test tracing with a simple agent with no tools or handoffs"""
    with request_vcr.use_cassette("test_simple_agent.yaml"):
        await agents.Runner.run(simple_agent, "What is the capital of France?")


@pytest.mark.snapshot
async def test_openai_agents_with_tool_error(agents, request_vcr, addition_agent_with_tool_errors):
    with request_vcr.use_cassette("test_agent_with_tool_errors.yaml"):
        await agents.Runner.run(addition_agent_with_tool_errors, "What is the sum of 1 and 2?")
