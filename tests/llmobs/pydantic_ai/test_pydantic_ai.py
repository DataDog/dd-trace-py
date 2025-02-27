import pytest


from ddtrace.contrib.internal.pydantic_ai.patch import patch
from ddtrace.contrib.internal.pydantic_ai.patch import unpatch


@pytest.fixture(autouse=True)
def pydantic_ai():
    patch()
    import pydantic_ai

    yield pydantic_ai
    unpatch()


async def test_agent_run(pydantic_ai, llmobs, llmobs_backend):
    agent = pydantic_ai.Agent(model="gpt-4o-mini")
    await agent.run("Hello, world!")

    events = llmobs_backend.wait_for_num_events(num=1)
    agent_span = [span for span in events[0]["spans"] if span["meta"]["span.kind"] == "agent"]
    assert len(agent_span) == 1


def test_agent_run_sync(pydantic_ai, llmobs, llmobs_backend):
    agent = pydantic_ai.Agent(model="gpt-4o-mini")
    agent.run_sync("Hello, world!")

    events = llmobs_backend.wait_for_num_events(num=1)
