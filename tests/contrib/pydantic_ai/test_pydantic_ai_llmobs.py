import mock
import pydantic_ai
import pytest
from typing_extensions import TypedDict

from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs._utils import safe_json
from tests.contrib.pydantic_ai.utils import PYDANTIC_AI_TAGS
from tests.contrib.pydantic_ai.utils import calculate_square_tool
from tests.contrib.pydantic_ai.utils import expected_agent_metadata
from tests.contrib.pydantic_ai.utils import expected_calculate_square_tool
from tests.contrib.pydantic_ai.utils import expected_foo_tool
from tests.contrib.pydantic_ai.utils import foo_tool
from tests.llmobs._utils import assert_llmobs_span_data


PYDANTIC_AI_VERSION = parse_version(pydantic_ai.__version__)

TOOL_DESCRIPTION_METADATA = {"description": "Calculates the square of a number"}


@pytest.mark.parametrize(
    "ddtrace_global_config",
    [dict(_llmobs_enabled=True, _llmobs_ml_app="<ml-app-name>")],
)
class TestLLMObsPydanticAI:
    async def test_agent_run(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
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
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=result.output,
            metadata=expected_agent_metadata(
                instructions=instructions,
                system_prompt=system_prompt,
                model_settings=model_settings,
                tools=expected_calculate_square_tool(),
            ),
            tags=PYDANTIC_AI_TAGS,
        )

    def test_agent_run_sync(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            result = agent.run_sync("Hello, world!")
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=result.output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
        )

    async def test_agent_run_stream(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        output = ""
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.run_stream("Hello, world!") as result:
                async for chunk in result.stream(debounce_by=None):
                    output = chunk
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
        )

    @pytest.mark.parametrize("delta", [False, True])
    async def test_agent_run_stream_text(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans, delta):
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
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
        )

    @pytest.mark.parametrize("stream_method", ["stream_structured", "stream_responses"])
    async def test_agent_run_stream_method(
        self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans, stream_method
    ):
        if stream_method == "stream_responses" and PYDANTIC_AI_VERSION < (0, 8, 1):
            pytest.skip("pydantic-ai < 0.8.1 does not support stream_responses")

        output = ""
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.run_stream("Hello, world!") as result:
                stream_func = getattr(result, stream_method)
                async for chunk in stream_func():
                    output = chunk[0].parts[0].content
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
        )

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (0, 8, 1), reason="pydantic-ai < 0.8.1 does not support stream_responses")
    async def test_agent_run_stream_responses_early_exit(
        self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans
    ):
        """Test that the span is still finished when the stream is exited early"""
        output = ""
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.run_stream("Hello, world!") as result:
                async for chunk, last in result.stream_responses():
                    assert not last  # assert this is not the last chunk
                    output = chunk.parts[0].content
                    break
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
        )

    async def test_agent_run_stream_get_output(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        output = ""
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.run_stream("Hello, world!") as result:
                output = await result.get_output()
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
        )

    async def test_agent_run_stream_with_tool(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        instructions = "Use the provided tool to calculate the square of 2."
        with request_vcr.use_cassette("agent_run_stream_with_tools.yaml"):
            agent = pydantic_ai.Agent(
                model="gpt-4o", name="test_agent", tools=[calculate_square_tool], instructions=instructions
            )
            async with agent.run_stream("What is the square of 2?") as result:
                async for chunk in result.stream():
                    output = chunk
        trace = test_spans.pop_traces()[0]
        agent_span_data = _get_llmobs_data_metastruct(trace[0])
        tool_span_data = _get_llmobs_data_metastruct(trace[1])
        assert_llmobs_span_data(
            agent_span_data,
            span_kind="agent",
            name="test_agent",
            input_value="What is the square of 2?",
            output_value=output,
            metadata=expected_agent_metadata(instructions=instructions, tools=expected_calculate_square_tool()),
            tags=PYDANTIC_AI_TAGS,
        )
        assert_llmobs_span_data(
            tool_span_data,
            span_kind="tool",
            name="calculate_square_tool",
            parent_id=str(trace[0].span_id),
            input_value='{"x":2}',
            output_value="4",
            metadata=TOOL_DESCRIPTION_METADATA,
            tags=PYDANTIC_AI_TAGS,
        )

    @pytest.mark.parametrize("stream_method", ["stream_structured", "stream_responses"])
    async def test_agent_run_stream_method_with_tool(
        self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans, stream_method
    ):
        if stream_method == "stream_responses" and PYDANTIC_AI_VERSION < (0, 8, 1):
            pytest.skip("pydantic-ai < 0.8.1 does not support stream_responses")

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
                stream_func = getattr(result, stream_method)
                async for chunk in stream_func(debounce_by=None):
                    output = chunk
        trace = test_spans.pop_traces()[0]
        agent_span_data = _get_llmobs_data_metastruct(trace[0])
        tool_span_data = _get_llmobs_data_metastruct(trace[1])
        assert_llmobs_span_data(
            agent_span_data,
            span_kind="agent",
            name="test_agent",
            input_value="What is the square of 2?",
            output_value=safe_json(output[0].parts[0].args, ensure_ascii=False),
            metadata=expected_agent_metadata(instructions=instructions, tools=expected_calculate_square_tool()),
            tags=PYDANTIC_AI_TAGS,
        )
        assert_llmobs_span_data(
            tool_span_data,
            span_kind="tool",
            name="calculate_square_tool",
            parent_id=str(trace[0].span_id),
            input_value='{"x":2}',
            output_value="4",
            metadata=TOOL_DESCRIPTION_METADATA,
            tags=PYDANTIC_AI_TAGS,
        )

    async def test_agent_run_stream_error(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        output = ""
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            with pytest.raises(Exception, match="test error"):
                async with agent.run_stream("Hello, world!") as result:
                    stream = result.stream(debounce_by=None)
                    async for chunk in stream:
                        output = chunk
                        raise Exception("test error")

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            input_value="Hello, world!",
            output_value=output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
            error={"type": "builtins.Exception", "message": "test error", "stack": mock.ANY},
        )

    async def test_agent_iter(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        output = ""
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.iter("Hello, world!") as agent_run:
                async for _ in agent_run:
                    pass
                output = agent_run.result.output
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
        )

    async def test_agent_iter_error(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            with pytest.raises(Exception, match="test error"):
                async with agent.iter("Hello, world!") as agent_run:
                    async for _ in agent_run:
                        raise Exception("test error")

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        agent_span_data = _get_llmobs_data_metastruct(spans[0])
        assert agent_span_data["meta"]["error"]["message"] == "test error"
        assert spans[0].error == 1

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (0, 4, 4), reason="pydantic-ai < 0.4.4 does not support toolsets")
    async def test_agent_run_with_toolset(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        """Test that the agent manifest includes tools from both the function toolset and the user-defined toolsets"""
        from pydantic_ai.toolsets import FunctionToolset

        with request_vcr.use_cassette("agent_run_stream_with_toolset.yaml"):
            agent = pydantic_ai.Agent(
                model="gpt-4o",
                name="test_agent",
                toolsets=[FunctionToolset(tools=[calculate_square_tool])],
                tools=[foo_tool],
            )
            result = await agent.run("Hello, world!")
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=result.output,
            metadata=expected_agent_metadata(tools=expected_calculate_square_tool() + expected_foo_tool()),
            tags=PYDANTIC_AI_TAGS,
        )

    async def test_agent_run_with_message_history(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        """Test that INPUT_VALUE is set from message_history when user_prompt is not provided."""
        from pydantic_ai.messages import ModelRequest
        from pydantic_ai.messages import UserPromptPart

        message_history = [ModelRequest(parts=[UserPromptPart(content="Hello from history!")])]
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            result = await agent.run(message_history=message_history)
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello from history!",
            output_value=result.output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
        )

    async def test_agent_run_stream_with_message_history(
        self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans
    ):
        """Test that INPUT_VALUE is set from message_history for run_stream."""
        from pydantic_ai.messages import ModelRequest
        from pydantic_ai.messages import UserPromptPart

        message_history = [ModelRequest(parts=[UserPromptPart(content="Hello from history!")])]
        output = ""
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.run_stream(message_history=message_history) as result:
                async for chunk in result.stream(debounce_by=None):
                    output = chunk
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello from history!",
            output_value=output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
        )

    async def test_agent_iter_with_message_history(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        """Test that INPUT_VALUE is set from message_history for iter."""
        from pydantic_ai.messages import ModelRequest
        from pydantic_ai.messages import UserPromptPart

        message_history = [ModelRequest(parts=[UserPromptPart(content="Hello from history!")])]
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.iter(message_history=message_history) as agent_run:
                async for _ in agent_run:
                    pass
                output = agent_run.result.output
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello from history!",
            output_value=output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
        )

    async def test_agent_run_with_user_prompt_and_message_history(
        self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans
    ):
        """Test that user_prompt takes precedence over message_history."""
        from pydantic_ai.messages import ModelRequest
        from pydantic_ai.messages import UserPromptPart

        message_history = [ModelRequest(parts=[UserPromptPart(content="Hello from history!")])]
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            result = await agent.run("Hello, world!", message_history=message_history)
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=result.output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
        )


class TestLLMObsPydanticAISpanLinks:
    async def test_agent_calls_tool(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, openai_patched, test_spans):
        instructions = "Use the provided tool to calculate the square of 2."
        with request_vcr.use_cassette("agent_run_stream_with_tools.yaml"):
            agent = pydantic_ai.Agent(
                model="gpt-4o", name="test_agent", tools=[calculate_square_tool], instructions=instructions
            )
            async with agent.run_stream("What is the square of 2?") as result:
                async for _ in result.stream(debounce_by=None):
                    pass

        trace = test_spans.pop_traces()[0]
        # APM trace order: agent → first LLM call → tool → second LLM call.
        first_llm_span = trace[1]
        tool_span = trace[2]
        second_llm_span = trace[3]
        tool_span_data = _get_llmobs_data_metastruct(tool_span)
        second_llm_span_data = _get_llmobs_data_metastruct(second_llm_span)

        # LLM-to-tool span link should be on the tool span, pointing at the first LLM span.
        assert len(tool_span_data["span_links"]) == 1
        assert tool_span_data["span_links"][0]["span_id"] == str(first_llm_span.span_id)
        assert tool_span_data["span_links"][0]["attributes"] == {"from": "output", "to": "input"}
        # tool-to-LLM span link should be on the second LLM span, pointing at the tool span.
        assert len(second_llm_span_data["span_links"]) == 1
        assert second_llm_span_data["span_links"][0]["span_id"] == str(tool_span.span_id)
        assert second_llm_span_data["span_links"][0]["attributes"] == {"from": "output", "to": "input"}
