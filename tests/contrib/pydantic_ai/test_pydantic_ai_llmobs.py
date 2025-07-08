import mock
import pytest
from typing_extensions import TypedDict

from ddtrace.llmobs._utils import safe_json
from tests.contrib.pydantic_ai.utils import calculate_square_tool
from tests.contrib.pydantic_ai.utils import expected_run_agent_span_event
from tests.contrib.pydantic_ai.utils import expected_run_tool_span_event
from tests.contrib.pydantic_ai.utils import get_usage
from tests.llmobs._utils import _expected_llmobs_non_llm_span_event


@pytest.mark.parametrize(
    "ddtrace_global_config",
    [dict(_llmobs_enabled=True, _llmobs_ml_app="<ml-app-name>", _llmobs_agentless_enabled=True)],
)
class TestLLMObsPydanticAI:
    async def test_agent_run(self, pydantic_ai, request_vcr, llmobs_events, mock_tracer):
        model_settings = {"max_tokens": 100, "temperature": 0.5}
        instructions = "dummy instructions"
        system_prompt = "dummy system prompt"
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(
                model="gpt-4o",
                name="test_agent",
                instructions=instructions,
                system_prompt=system_prompt,
                tools=[calculate_square_tool],
                model_settings=model_settings,
            )
            result = await agent.run("Hello, world!")
        token_metrics = get_usage(result)
        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected_run_agent_span_event(
            span,
            result.output,
            token_metrics,
            instructions=instructions,
            system_prompt=system_prompt,
            tools=["calculate_square_tool"],
            model_settings=model_settings,
        )

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
                async for chunk in result.stream(debounce_by=None):
                    output = chunk
        token_metrics = get_usage(result)
        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected_run_agent_span_event(span, output, token_metrics)

    @pytest.mark.parametrize("delta", [False, True])
    async def test_agent_run_stream_text(self, pydantic_ai, request_vcr, llmobs_events, mock_tracer, delta):
        """
        delta determines whether each chunk represents the entire output up to the current point or just the
        delta from the previous chunk
        """
        output = ""
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.run_stream("Hello, world!") as result:
                async for chunk in result.stream_text(debounce_by=None, delta=delta):
                    output = output + chunk if delta else chunk
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

    async def test_agent_run_stream_with_tool(self, pydantic_ai, request_vcr, llmobs_events, mock_tracer):
        instructions = "Use the provided tool to calculate the square of 2."
        with request_vcr.use_cassette("agent_run_stream_with_tools.yaml"):
            agent = pydantic_ai.Agent(
                model="gpt-4o", name="test_agent", tools=[calculate_square_tool], instructions=instructions
            )
            async with agent.run_stream("What is the square of 2?") as result:
                async for chunk in result.stream():
                    output = chunk
        token_metrics = get_usage(result)
        trace = mock_tracer.pop_traces()[0]
        agent_span = trace[0]
        tool_span = trace[1]
        assert len(llmobs_events) == 2
        assert llmobs_events[0] == expected_run_tool_span_event(tool_span, span_links=True)
        assert llmobs_events[1] == expected_run_agent_span_event(
            agent_span,
            output,
            token_metrics,
            input_value="What is the square of 2?",
            instructions=instructions,
            tools=["calculate_square_tool"],
            span_links=True,
        )

    async def test_agent_run_stream_structured_with_tool(self, pydantic_ai, request_vcr, llmobs_events, mock_tracer):
        class Output(TypedDict):
            original_number: int
            square: int

        instructions = "Use the provided tool to calculate the square of 2."
        with request_vcr.use_cassette("agent_run_stream_structured_with_tool.yaml"):
            agent = pydantic_ai.Agent(
                model="gpt-4o",
                name="test_agent",
                tools=[calculate_square_tool],
                instructions=instructions,
                output_type=Output,
            )
            async with agent.run_stream("What is the square of 2?") as result:
                async for chunk in result.stream_structured(debounce_by=None):
                    output = chunk
        token_metrics = get_usage(result)
        trace = mock_tracer.pop_traces()[0]
        agent_span = trace[0]
        tool_span = trace[1]
        assert len(llmobs_events) == 2
        assert llmobs_events[0] == expected_run_tool_span_event(tool_span, span_links=True)
        assert llmobs_events[1] == expected_run_agent_span_event(
            agent_span,
            safe_json(output[0].parts[0].args, ensure_ascii=False),
            token_metrics,
            input_value="What is the square of 2?",
            instructions=instructions,
            tools=["calculate_square_tool"],
            span_links=True,
        )

    async def test_agent_run_stream_error(self, pydantic_ai, request_vcr, llmobs_events, mock_tracer):
        output = ""
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            with pytest.raises(Exception, match="test error"):
                async with agent.run_stream("Hello, world!") as result:
                    stream = result.stream(debounce_by=None)
                    async for chunk in stream:
                        output = chunk
                        raise Exception("test error")

        span = mock_tracer.pop_traces()[0][0]
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == _expected_llmobs_non_llm_span_event(
            span,
            "agent",
            input_value="Hello, world!",
            output_value=output,
            metadata={"instructions": None, "system_prompts": (), "tools": []},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.pydantic_ai"},
            error="builtins.Exception",
            error_message="test error",
            error_stack=mock.ANY,
        )

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

    async def test_agent_iter_error(self, pydantic_ai, request_vcr, llmobs_events):
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            with pytest.raises(Exception, match="test error"):
                async with agent.iter("Hello, world!") as agent_run:
                    async for _ in agent_run:
                        raise Exception("test error")

        assert len(llmobs_events) == 1
        assert llmobs_events[0]["status"] == "error"
        assert llmobs_events[0]["meta"]["error.message"] == "test error"


class TestLLMObsPydanticAISpanLinks:
    async def test_agent_calls_tool(self, pydantic_ai, request_vcr, llmobs_events):
        instructions = "Use the provided tool to calculate the square of 2."
        with request_vcr.use_cassette("agent_run_stream_with_tools.yaml"):
            agent = pydantic_ai.Agent(
                model="gpt-4o", name="test_agent", tools=[calculate_square_tool], instructions=instructions
            )
            async with agent.run_stream("What is the square of 2?") as result:
                async for _ in result.stream(debounce_by=None):
                    pass
        assert len(llmobs_events) == 2
        # agent to tool span link should be present on the tool span
        assert len(llmobs_events[0]["span_links"]) == 1
        assert llmobs_events[0]["span_links"][0]["span_id"] == llmobs_events[1]["span_id"]
        assert llmobs_events[0]["span_links"][0]["attributes"] == {"from": "input", "to": "input"}
        # tool to agent span link should be present on the agent span
        assert len(llmobs_events[1]["span_links"]) == 1
        assert llmobs_events[1]["span_links"][0]["span_id"] == llmobs_events[0]["span_id"]
        assert llmobs_events[1]["span_links"][0]["attributes"] == {"from": "output", "to": "output"}
