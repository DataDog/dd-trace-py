import pytest
from tests.contrib.pydantic_ai.utils import expected_run_agent_span_event, get_usage

@pytest.mark.parametrize(
    "ddtrace_global_config",
    [dict(_llmobs_enabled=True, _llmobs_ml_app="<ml-app-name>", _llmobs_agentless_enabled=True)],
)
class TestLLMObsPydanticAI:
    async def test_agent_run(self, pydantic_ai, request_vcr, llmobs_events, mock_tracer):
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            result = await agent.run("Hello, world!")        
        token_metrics = get_usage(result)
        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected_run_agent_span_event(span, result.output, token_metrics)


    def test_agent_run_sync(self, pydantic_ai, request_vcr, llmobs_events, mock_tracer):
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            result = agent.run_sync("Hello, world!")
        token_metrics = get_usage(result)
        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected_run_agent_span_event(span, result.output, token_metrics)


    async def test_agent_run_stream(self, pydantic_ai, request_vcr, llmobs_events, mock_tracer):
        output = ""
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.run_stream("Hello, world!") as result:
                async for chunk in result.stream():
                    output = chunk
        token_metrics = get_usage(result)
        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected_run_agent_span_event(span, output, token_metrics)
    
    async def test_agent_run_stream_text(self, pydantic_ai, request_vcr, llmobs_events, mock_tracer):
        output = ""
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.run_stream("Hello, world!") as result:
                async for chunk in result.stream_text():
                    output = chunk
        token_metrics = get_usage(result)
        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected_run_agent_span_event(span, output, token_metrics)
    
    async def test_agent_run_stream_structured(self, pydantic_ai, request_vcr, llmobs_events, mock_tracer):
        output = ""
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.run_stream("Hello, world!") as result:
                async for chunk in result.stream_structured():
                    if chunk[1]:
                        output = chunk[0].parts[0].content
        token_metrics = get_usage(result)
        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected_run_agent_span_event(span, output, token_metrics)
    
    async def test_agent_run_stream_get_output(self, pydantic_ai, request_vcr, llmobs_events, mock_tracer):
        output = ""
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.run_stream("Hello, world!") as result:
                output = await result.get_output()
        token_metrics = get_usage(result)
        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected_run_agent_span_event(span, output, token_metrics)

    async def test_agent_iter(self, pydantic_ai, request_vcr, llmobs_events, mock_tracer):
        output = ""
        token_metrics = {}
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.iter("Hello, world!") as agent_run:
                async for _ in agent_run:
                    pass
                output = agent_run.result.output
                token_metrics = get_usage(agent_run.result)
        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected_run_agent_span_event(span, output, token_metrics)
