import json
from typing import Literal
from typing import Optional

import mock
from pydantic import BaseModel
import pydantic_ai
import pytest
from typing_extensions import TypedDict

from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs._utils import safe_json
from tests.contrib.pydantic_ai.utils import MANIFEST_VERSION
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
                model_params=model_settings,
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
            metadata=expected_agent_metadata(
                instructions=instructions,
                tools=expected_calculate_square_tool(),
                # A TypedDict output yields a name but no schema, since only a pydantic model has one.
                data_contracts={"output": {"name": "Output"}},
            ),
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

    async def test_agent_run_with_unserializable_model_settings(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """Regression test: agent.model_settings containing non-JSON-serializable provider
        sentinel values must not crash span submission.

        Uses FunctionModel to avoid OpenAI SDK serialization, which would reject the
        sentinel before our span-tagging code ever runs.
        """
        agent = pydantic_ai.Agent(
            model=_function_model(),
            name="test_agent",
            model_settings={"temperature": _UnserializableSentinel(), "max_tokens": 100},
        )
        await agent.run("Hello, world!")
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        settings = _manifest_of(spans[0])["model_settings"]
        # The whole manifest has to survive the encoder, which is what the sentinel used to break.
        json.dumps(_manifest_of(spans[0]))
        assert settings["max_tokens"] == 100
        # A sentinel stands for "not set", so the field is absent rather than holding the string
        # "Omit()" in a slot that is meant to hold a number.
        assert "temperature" not in settings


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


class _UnserializableSentinel:
    """Stand-in for provider sentinels such as OpenAI's ``Omit`` / ``NOT_GIVEN``."""

    def __repr__(self):
        return "Omit()"


def _test_model():
    """A model that synthesises schema-valid output, needed when output_type is a function."""
    from pydantic_ai.models.test import TestModel

    return TestModel()


def _function_model():
    """A model that answers locally, so a manifest test needs no cassette and no network."""
    from pydantic_ai.messages import ModelResponse
    from pydantic_ai.messages import TextPart
    from pydantic_ai.models.function import FunctionModel

    def model_func(messages, info):
        return ModelResponse(parts=[TextPart(content="Hello!")])

    return FunctionModel(model_func)


def _manifest_of(span):
    return _get_llmobs_data_metastruct(span)["meta"]["metadata"]["_dd"]["agent_manifest"]


def _without_hashes(node):
    """Drop source_hash everywhere so a field mapping can be compared exactly.

    The hash covers formatting and file position, so it is not stable enough to assert by value.
    test_function_source_is_hashed_never_emitted covers the hash itself.
    """
    if isinstance(node, dict):
        return {k: _without_hashes(v) for k, v in node.items() if k != "source_hash"}
    if isinstance(node, list):
        return [_without_hashes(v) for v in node]
    return node


def _assert_contains(manifest, expected, path=""):
    """Assert every grouping and field in expected matches, ignoring anything not mentioned.

    Entries in a list are compared the same way, so a case pins only the fields it is about. That
    matters for builtin_tools, whose name is "WebSearchTool" below 1.63.0 and "web_search" from there.
    """
    for key, want in expected.items():
        assert key in manifest, "manifest is missing {}{}".format(path, key)
        got = manifest[key]
        if isinstance(want, dict) and isinstance(got, dict):
            _assert_contains(got, want, "{}{}.".format(path, key))
        elif isinstance(want, list) and isinstance(got, list):
            assert len(got) == len(want), "{}{}: expected {} entries, got {}".format(path, key, len(want), len(got))
            for index, (want_entry, got_entry) in enumerate(zip(want, got)):
                if isinstance(want_entry, dict) and isinstance(got_entry, dict):
                    _assert_contains(got_entry, want_entry, "{}{}[{}].".format(path, key, index))
                else:
                    assert got_entry == want_entry, "{}{}[{}]".format(path, key, index)
        else:
            assert got == want, "{}{}: expected {!r}, got {!r}".format(path, key, want, got)


# What must never reach the wire, one case per carrier: (kwargs factory, forbidden substrings,
# manifest subset that must still be present, minimum pydantic-ai version). Collected in one table so
# the security contract is reviewable in a single place.
MANIFEST_LEAK_CASES = [
    pytest.param(
        lambda: dict(
            model_settings={
                "temperature": 0.5,
                "extra_headers": {"Authorization": "Bearer sk-leak-canary"},
                "extra_body": {"credential": "sk-leak-canary-2"},
            }
        ),
        ["sk-leak-canary", "Authorization", "extra_headers", "extra_body"],
        {"model_settings": {"temperature": 0.5}},
        None,
        id="transport_params_never_ship",
    ),
    pytest.param(
        lambda: dict(
            model_settings={
                "temperature": 0.5,
                "anthropic_metadata": {"user_id": "user-42-pii"},
                "bedrock_request_metadata": {"trace_token": "sk-leak-canary"},
                "openai_user": "end-user-99",
            }
        ),
        ["sk-leak-canary", "user-42-pii", "end-user-99"],
        {"model_settings": {"temperature": 0.5}},
        None,
        id="provider_blobs_never_ship",
    ),
    pytest.param(
        # The shared schema has no field for validation_context, so it drops entirely rather than
        # shipping key names. It accepts Any, and a dict there routinely holds a live client or a key.
        lambda: dict(validation_context={"tenant": "acme", "api_key": "sk-leak-canary"}),
        ["sk-leak-canary", "validation_context"],
        {},
        (1, 63, 0),
        id="validation_context_never_ships",
    ),
]


class _Deps:
    tenant: str


def _redact_history(messages):
    """Strip personal data from history."""
    return messages


def _tenant_toolset(ctx):
    """Load the tenant's toolset."""
    return None


def _escalate(reason: str) -> str:
    """Hand the ticket to a human."""
    return reason


def _builtin_web_search():
    from pydantic_ai.builtin_tools import WebSearchTool

    return WebSearchTool(search_context_size="high", max_uses=3)


# One field mapping per case: (kwargs factory, expected manifest subset, minimum pydantic-ai version).
# A factory rather than a literal so version-gated imports happen only when the case runs.
MANIFEST_FIELD_CASES = [
    pytest.param(
        lambda: dict(model_settings={"temperature": 0, "parallel_tool_calls": False, "max_tokens": 0}),
        # Falsy is not absent. Filtering on truthiness is what loses a deliberate temperature of 0.
        {"model_settings": {"temperature": 0, "parallel_tool_calls": False, "max_tokens": 0}},
        None,
        id="falsy_model_params_survive",
    ),
    pytest.param(
        lambda: dict(model_settings={"temperature": 0.5, "stop_sequences": ["END"], "timeout": 30.0}),
        {"model_settings": {"temperature": 0.5, "stop_sequences": ["END"], "timeout": 30.0}},
        None,
        id="allowlisted_params_pass_through_unrenamed",
    ),
    pytest.param(
        # A provider-prefixed key is not on the allowlist, so it drops rather than being promoted.
        lambda: dict(model_settings={"openai_reasoning_effort": "high"}),
        {},
        None,
        id="provider_prefixed_param_drops",
    ),
    pytest.param(
        lambda: dict(history_processors=[_redact_history]),
        {"memory_policies": [{"name": "_redact_history", "content": {"source_hash": mock.ANY}}]},
        None,
        id="history_processors_land_in_memory_policies",
    ),
    pytest.param(
        lambda: dict(toolsets=[_tenant_toolset]),
        {"capabilities": [{"name": "_tenant_toolset", "type": "custom", "content": {"dynamic": True}}]},
        (0, 4, 4),
        id="dynamic_toolset_is_a_custom_capability",
    ),
    pytest.param(
        lambda: dict(builtin_tools=[_builtin_web_search()]),
        {
            "capabilities": [
                {
                    "name": mock.ANY,
                    "type": "builtin",
                    "content": {"config": {"max_uses": 3, "search_context_size": "high"}},
                }
            ]
        },
        None,
        id="builtin_tool_config_is_captured",
    ),
    pytest.param(
        lambda: dict(tool_timeout=12.5, max_concurrency=4),
        {"agent_settings": {"tool_timeout": 12.5, "max_concurrency": 4}},
        (1, 63, 0),
        id="tool_timeout_and_max_concurrency",
    ),
    pytest.param(
        lambda: dict(metadata={"suite": "manifest", "owner": "llmobs"}),
        {"metadata": {"suite": "manifest", "owner": "llmobs"}},
        (1, 63, 0),
        id="metadata_is_top_level",
    ),
    pytest.param(
        lambda: dict(deps_type=_Deps, end_strategy="exhaustive"),
        {"agent_settings": {"deps_type": "_Deps", "end_strategy": "exhaustive"}},
        None,
        id="deps_type_and_end_strategy_land_in_agent_settings",
    ),
    pytest.param(
        lambda: dict(model=_test_model(), output_type=[_escalate]),
        {},
        None,
        id="output_function_does_not_become_a_handoff",
    ),
]


def _empty_paths(node, path="manifest"):
    """Every path in the manifest whose value is None or an empty container.

    Used instead of spot-checking known keys, so a newly added grouping cannot quietly start emitting
    nulls without a test noticing.
    """
    if node is None or (isinstance(node, (str, bytes, list, tuple, dict, set)) and len(node) == 0):
        return [path]
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            found.extend(_empty_paths(value, "{}.{}".format(path, key)))
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            found.extend(_empty_paths(value, "{}[{}]".format(path, index)))
    return found


@pytest.mark.parametrize(
    "ddtrace_global_config",
    [dict(_llmobs_enabled=True, _llmobs_ml_app="<ml-app-name>")],
)
class TestPydanticAIAgentManifest:
    """The agent manifest as a contract: shape, omissions, and what must never ship.

    These go through a real agent.run() so the manifest is read off the span the customer would get,
    not off a direct builder call.
    """

    # Every top-level key the shared schema allows. A key outside this set is either a typo or an
    # invention, and both are caught by test_shape_is_one_flat_document rather than by review.
    SCHEMA_KEYS = frozenset(
        {
            "manifest_version",
            "framework",
            "name",
            "description",
            "metadata",
            "model",
            "model_settings",
            "instructions",
            "system_prompts",
            "extra_instructions",
            "tools",
            "capabilities",
            "data_contracts",
            "memory_policies",
            "guardrails",
            "handoffs",
            "agent_settings",
        }
    )

    async def _run(self, pydantic_ai, test_spans, **agent_kwargs):
        agent_kwargs.setdefault("model", _function_model())
        agent = pydantic_ai.Agent(**agent_kwargs)
        await agent.run("Hello, world!")
        # The agent span is the root of the trace. A structured output can add a tool span underneath
        # it on some versions, so index rather than asserting a span count.
        trace = test_spans.pop_traces()[0]
        return agent, _manifest_of(trace[0])

    @pytest.mark.parametrize("make_kwargs,expected,min_version", MANIFEST_FIELD_CASES)
    async def test_field_mapping_cases(
        self, pydantic_ai, pydantic_ai_llmobs, test_spans, make_kwargs, expected, min_version
    ):
        """One case per field mapping, asserted as a subset so unrelated keys do not couple."""
        if min_version and PYDANTIC_AI_VERSION < min_version:
            pytest.skip("pydantic-ai < {} does not support this field".format(min_version))

        _, manifest = await self._run(pydantic_ai, test_spans, name="test_agent", **make_kwargs())

        _assert_contains(manifest, expected)

    @pytest.mark.parametrize("make_kwargs,forbidden,expected,min_version", MANIFEST_LEAK_CASES)
    async def test_secrets_never_ship_cases(
        self, pydantic_ai, pydantic_ai_llmobs, test_spans, make_kwargs, forbidden, expected, min_version
    ):
        """The security contract, one case per carrier, in one reviewable table.

        expected is asserted alongside so a case cannot pass by emitting nothing at all.
        """
        if min_version and PYDANTIC_AI_VERSION < min_version:
            pytest.skip("pydantic-ai < {} does not support this field".format(min_version))

        _, manifest = await self._run(pydantic_ai, test_spans, name="test_agent", **make_kwargs())

        blob = safe_json(manifest)
        for canary in forbidden:
            assert canary not in blob, "{} reached the manifest".format(canary)
        _assert_contains(manifest, expected)

    async def test_shape_is_one_flat_document(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """One flat document whose keys all come from the shared schema.

        Driven off a richly-configured agent, because a minimal one has too few keys to test anything.
        """

        class Deps:
            tenant: str

        _, manifest = await self._run(
            pydantic_ai,
            test_spans,
            name="test_agent",
            instructions="Stay terse.",
            system_prompt="Cite sources.",
            tools=[calculate_square_tool],
            deps_type=Deps,
            model_settings={"temperature": 0.5, "logit_bias": {50256: -100}, "timeout": 30.0},
        )

        assert manifest["manifest_version"] == MANIFEST_VERSION
        unknown = set(manifest) - self.SCHEMA_KEYS
        assert not unknown, "manifest emits keys outside the shared schema: {}".format(sorted(unknown))
        # No key is a dotted path: the flat schema has no prefixed names to parse apart.
        assert not [key for key in manifest if "." in key]

    async def test_framework_is_the_display_name(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """framework keeps the value it has always had.

        Re-keying it to the integration token would silently break every consumer filtering on
        framework, for no gain the schema migration needs.
        """
        _, manifest = await self._run(pydantic_ai, test_spans, name="test_agent")
        assert manifest["framework"] == "PydanticAI"

    async def test_field_mapping(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """The whole document for a configured agent, compared exactly.

        An exact comparison is what catches a field quietly moving or gaining a wrapper. Hashes are
        stripped because they are asserted on their own in test_function_source_is_hashed_never_emitted.
        """

        class Deps:
            tenant: str

        class Resolution(BaseModel):
            answer: str

        agent, manifest = await self._run(
            pydantic_ai,
            test_spans,
            name="support_orchestrator",
            instructions="You orchestrate specialists.",
            system_prompt="Cite sources.",
            tools=[calculate_square_tool],
            output_type=Resolution,
            deps_type=Deps,
            # A plain-text model cannot satisfy a structured output_type; it exhausts output retries.
            model=_test_model(),
            retries=3,
            end_strategy="exhaustive",
            model_settings={"temperature": 0.2, "max_tokens": 1024},
        )

        actual = _without_hashes(manifest)
        assert actual["manifest_version"] == MANIFEST_VERSION
        assert actual["framework"] == "PydanticAI"
        assert actual["name"] == "support_orchestrator"
        assert actual["instructions"] == "You orchestrate specialists."
        assert actual["system_prompts"] == ["Cite sources."]
        assert actual["model_settings"] == {"temperature": 0.2, "max_tokens": 1024}
        assert actual["tools"] == expected_calculate_square_tool()
        assert actual["data_contracts"] == {"output": {"name": "Resolution", "schema": mock.ANY}}
        assert actual["agent_settings"]["end_strategy"] == "exhaustive"
        assert actual["agent_settings"]["deps_type"] == "Deps"
        assert agent.model_settings["max_tokens"] == 1024, "the caller's own dict was mutated"

    async def test_secrets_never_ship(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """A provider passthrough carrying a credential must not reach the manifest.

        Reproduced on origin/main before this change: extra_headers was copied verbatim, so an
        Authorization header was submitted with the span. The manifest lands under metadata._dd, which
        a customer's own span processor never sees, so they cannot scrub it themselves.
        """
        _, manifest = await self._run(
            pydantic_ai,
            test_spans,
            name="test_agent",
            model_settings={
                "temperature": 0.2,
                "extra_headers": {"Authorization": "Bearer sk-canary-header"},
                "extra_body": {"signed": "sk-canary-body"},
                "openai_user": "end-user-4711",
            },
        )

        blob = safe_json(manifest)
        for canary in ("sk-canary-header", "sk-canary-body", "end-user-4711", "Authorization"):
            assert canary not in blob, "{} reached the manifest".format(canary)
        # The tuning param alongside them still ships, so the filter is selective, not a blanket drop.
        assert manifest["model_settings"] == {"temperature": 0.2}

    async def test_model_settings_is_an_allowlist(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """A key the allowlist does not name drops, even though nothing denies it by name.

        This is the difference that matters: a denylist has to enumerate an open set, and providers
        keep adding passthroughs. A parameter invented after this test was written still drops.
        """
        _, manifest = await self._run(
            pydantic_ai,
            test_spans,
            name="test_agent",
            model_settings={"temperature": 0.2, "some_future_provider_blob": {"token": "sk-unknown"}},
        )

        assert manifest["model_settings"] == {"temperature": 0.2}
        assert "sk-unknown" not in safe_json(manifest)

    async def test_allowlisted_key_still_drops_a_blob_value(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """The allowlist protects the key; a shape check protects the value.

        logit_bias and tool_choice take a caller-supplied mapping, which is the same unbounded shape
        the transport passthroughs use to carry credentials. A nested structure, or a mapping holding
        anything other than numbers, drops rather than shipping whatever it holds.
        """
        _, manifest = await self._run(
            pydantic_ai,
            test_spans,
            name="test_agent",
            model_settings={
                "temperature": 0.2,
                # A real logit_bias is token id to bias, so a string value here is already invalid.
                "logit_bias": {"tok": "sk-canary-in-a-value"},
                "tool_choice": {"function": {"nested": "sk-canary-nested"}},
            },
        )

        assert manifest["model_settings"] == {"temperature": 0.2}
        blob = safe_json(manifest)
        assert "sk-canary-in-a-value" not in blob
        assert "sk-canary-nested" not in blob

    async def test_legitimate_container_values_survive(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """The shape check must not swallow the container values that are genuinely inference config.

        Without this the previous test would pass trivially by dropping every container.
        """
        _, manifest = await self._run(
            pydantic_ai,
            test_spans,
            name="test_agent",
            model_settings={"stop_sequences": ["END", "STOP"], "logit_bias": {50256: -100}},
        )

        assert manifest["model_settings"]["stop_sequences"] == ["END", "STOP"]
        assert manifest["model_settings"]["logit_bias"] == {"50256": -100}

    async def test_unnamed_agent_reports_the_placeholder_name(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """An agent with no recoverable name still reports a name, per review.

        A consumer needs something to display, and the span name already falls back to the same
        literal, so the manifest agreeing with it beats an absent key. The consequence is that two
        distinct unnamed agents share this name, which is why name is not identity for versioning.

        The agents are held in a list so pydantic-ai's own name inference, which scans the calling
        frame for a variable bound to the agent, finds nothing.
        """
        agents = [pydantic_ai.Agent(model=_function_model())]
        await agents[0].run("Hello, world!")
        manifest = _manifest_of(test_spans.pop_traces()[0][0])

        assert manifest["name"] == "PydanticAI Agent"

    async def test_agent_name_may_be_inferred_by_the_framework(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """pydantic-ai infers a name from the calling frame, and the builder cannot tell it apart.

        Pinned rather than fixed: by the time the manifest is built an inferred name is
        indistinguishable from a declared one. Recorded so the limitation is not rediscovered.
        """
        agent = pydantic_ai.Agent(model=_function_model())
        await agent.run("Hello, world!")
        manifest = _manifest_of(test_spans.pop_traces()[0][0])

        assert manifest["name"] == "agent"

    async def test_minimal_agent_emits_nothing_empty(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """No key ships as null, "", [] or {}.

        Absence has to mean "not configured", which it cannot if an unconfigured field ships empty.
        origin/main emitted instructions: null for exactly this agent. Walks the whole document, so
        a new field cannot regress this.
        """
        _, manifest = await self._run(pydantic_ai, test_spans)

        def walk(node, path):
            if isinstance(node, dict):
                for key, value in node.items():
                    assert value is not None, "{}.{} is null".format(path, key)
                    assert value != "" and value != [] and value != {}, "{}.{} is empty".format(path, key)
                    walk(value, "{}.{}".format(path, key))
            elif isinstance(node, list):
                for index, item in enumerate(node):
                    walk(item, "{}[{}]".format(path, index))

        walk(manifest, "manifest")

    async def test_function_tool_appears_once_per_key(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """A function tool appears once in tools and once in capabilities, never twice within either.

        The duplication across the two keys is deliberate: tools stays for backward compatibility and
        capabilities is the typed superset. Duplication inside a key would double-count it.
        """
        _, manifest = await self._run(
            pydantic_ai, test_spans, name="test_agent", tools=[calculate_square_tool, foo_tool]
        )

        tool_names = [tool["name"] for tool in manifest["tools"]]
        assert sorted(tool_names) == sorted(set(tool_names))
        capability_names = [c["name"] for c in manifest["capabilities"] if c["type"] == "tool"]
        assert sorted(capability_names) == sorted(set(capability_names))
        assert sorted(capability_names) == sorted(tool_names)

    async def test_capabilities_are_typed(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """Every capability carries a name and a type from the closed set."""
        _, manifest = await self._run(pydantic_ai, test_spans, name="test_agent", tools=[calculate_square_tool])

        for capability in manifest["capabilities"]:
            assert capability["name"]
            assert capability["type"] in {"tool", "mcp", "builtin", "custom", "tool_preparation"}

    async def test_extra_instructions_carry_type_name_and_hash(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """A dynamic resolver ships as {type, name, content}, not as a boolean.

        Instructions are half the agent version fingerprint. A boolean "is dynamic" would say the text
        varies between runs but not how, so a prompt change inside a resolver would be invisible.
        """
        agent = pydantic_ai.Agent(model=_function_model(), name="test_agent")

        @agent.instructions
        def per_tenant_policy() -> str:
            """Inject the tenant policy at run time."""
            return "policy"

        await agent.run("Hello, world!")
        manifest = _manifest_of(test_spans.pop_traces()[0][0])

        entries = manifest["extra_instructions"]
        assert [entry["name"] for entry in entries] == ["per_tenant_policy"]
        assert entries[0]["type"] == "dynamic_instructions"
        assert entries[0]["content"]["source_hash"]
        assert entries[0]["content"]["reevaluated"] is True

    async def test_function_source_is_hashed_never_emitted(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """A callable becomes {name, source_hash}. Its body never reaches the wire.

        A function body can hold a literal secret, so hashing is what makes a change detectable without
        shipping the code that changed.
        """
        agent = pydantic_ai.Agent(model=_function_model(), name="test_agent")

        @agent.output_validator
        def reject_ungrounded(value):
            """A distinctive marker: SENTINEL_SOURCE_MUST_NOT_SHIP."""
            return value

        await agent.run("Hello, world!")
        manifest = _manifest_of(test_spans.pop_traces()[0][0])

        blob = safe_json(manifest)
        assert "SENTINEL_SOURCE_MUST_NOT_SHIP" not in blob
        assert "def reject_ungrounded" not in blob
        guardrail = manifest["guardrails"][0]
        assert guardrail["name"] == "reject_ungrounded"
        assert len(guardrail["content"]["source_hash"]) == 64

    async def test_data_contracts_carry_the_output_type(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """The declared output type lands under data_contracts.output as {name, schema}.

        pydantic-ai declares no input schema, so only the output half is ever populated.
        """

        class Resolution(BaseModel):
            answer: str
            confidence: float

        _, manifest = await self._run(
            pydantic_ai, test_spans, name="test_agent", output_type=Resolution, model=_test_model()
        )

        output = manifest["data_contracts"]["output"]
        assert output["name"] == "Resolution"
        assert output["schema"]["properties"].keys() == {"answer", "confidence"}
        assert "input" not in manifest["data_contracts"]

    async def test_agent_settings_carry_the_loop_knobs(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """retries is the output-validation budget and tool_retries the per-tool one.

        pydantic-ai keeps them separate: Agent(retries=3, output_retries=2) is retries 2 with
        tool_retries 3, not one number. Collapsing them would report a budget the agent does not use.
        """
        _, manifest = await self._run(
            pydantic_ai, test_spans, name="test_agent", retries=3, output_retries=2, end_strategy="exhaustive"
        )

        settings = manifest["agent_settings"]
        assert settings["retries"] == 2
        assert settings["tool_retries"] == 3
        assert settings["end_strategy"] == "exhaustive"

    async def test_no_handoffs_for_pydantic_ai(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """pydantic-ai declares no handoff parameter, so the key is absent rather than synthesized.

        Delegation here is one agent called inside another's tool, which is code and not declared
        config. Deriving handoff targets from output-function callables would be inferring from
        behavior, which is the one thing a manifest must not do.
        """
        _, manifest = await self._run(pydantic_ai, test_spans, name="test_agent", tools=[calculate_square_tool])

        assert "handoffs" not in manifest

    async def test_declared_string_model_is_read(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """A deferred model check leaves model as a string, and the name is still recovered.

        origin/main dropped the model entirely for these agents, because it only handled the resolved
        object form.
        """
        agent = pydantic_ai.Agent("openai:gpt-4o", name="test_agent", defer_model_check=True)
        # The manifest must report the declared value, not whatever served this one call.
        await agent.run("Hello, world!", model=_function_model())
        manifest = _manifest_of(test_spans.pop_traces()[0][0])

        assert manifest["model"] == "gpt-4o"

    @pytest.mark.parametrize(
        "declared,expected",
        [
            ("openai:gpt-4o", "gpt-4o"),
            # A bedrock or azure model name contains its own colon. Splitting on the last one reports
            # the version suffix as the model, which is wrong data rather than missing data.
            ("bedrock:anthropic.claude-v1:0", "anthropic.claude-v1:0"),
            ("gpt-4o", "gpt-4o"),
        ],
    )
    async def test_declared_model_string_splits_on_the_first_colon(
        self, pydantic_ai, pydantic_ai_llmobs, test_spans, declared, expected
    ):
        agent = pydantic_ai.Agent(declared, name="test_agent", defer_model_check=True)
        await agent.run("Hello, world!", model=_function_model())
        manifest = _manifest_of(test_spans.pop_traces()[0][0])

        assert manifest["model"] == expected

    async def test_non_string_description_never_ships(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """description takes whatever the caller passed, so a non-string would ship as a repr.

        The same guard system_prompts and metadata already have; description was the one that lacked it.
        """
        agent = pydantic_ai.Agent(model=_function_model(), name="test_agent")
        object.__setattr__(agent, "_description", _UnserializableSentinel())
        await agent.run("Hello, world!")
        manifest = _manifest_of(test_spans.pop_traces()[0][0])

        assert "description" not in manifest
        assert "object at 0x" not in safe_json(manifest)

    async def test_non_string_system_prompts_never_ship(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """pydantic-ai does not validate system_prompt, so a non-string would ship as a repr.

        A repr leaks a memory address and whatever the object's __repr__ chooses to include.
        """
        agent = pydantic_ai.Agent(model=_function_model(), name="test_agent", system_prompt="real prompt")
        agent._system_prompts = ("real prompt", _UnserializableSentinel())
        # Built directly rather than through agent.run(). pydantic-ai itself cannot run an agent whose
        # system prompts are not all strings, so the span path can never reach the builder with one.
        # The guard is defense in depth: the attribute is public and unvalidated, so a framework change
        # or a caller reaching in gets filtered rather than shipping a repr with a memory address.
        # The integration instance is the one the patch installed, so no config has to be synthesized.
        manifest = pydantic_ai._datadog_integration._build_agent_manifest(agent)

        assert manifest["system_prompts"] == ["real prompt"]
        assert "object at 0x" not in safe_json(manifest)

    async def test_wire_values_are_json_native(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """Only JSON-native values reach the wire.

        The manifest travels in meta_struct, so an unencodable object there fails the whole span at
        encode time, taking the customer's trace with it.
        """
        _, manifest = await self._run(
            pydantic_ai,
            test_spans,
            name="test_agent",
            model_settings={"temperature": 0.2, "logit_bias": {50256: -100}},
        )

        json.dumps(manifest)

    async def test_non_finite_floats_never_ship(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """NaN and Infinity are valid Python floats and invalid JSON.

        The key drops rather than shipping null. json.dumps alone does not catch that, because a null
        encodes fine: an earlier version of this test passed while NaN was landing as an explicit null.
        """
        _, manifest = await self._run(
            pydantic_ai,
            test_spans,
            name="test_agent",
            model_settings={"temperature": float("nan"), "top_p": 0.9},
        )

        json.dumps(manifest, allow_nan=False)
        assert manifest["model_settings"] == {"top_p": 0.9}

    def test_mcp_servers_are_filtered_named_and_scrubbed(self, pydantic_ai):
        """MCP capture, which no other test reaches: the mcp extra is in none of the riot venvs.

        Stubbing the class lookup rather than the extra keeps the real filtering, naming and URL
        scrubbing under test. A stdio server has no url, so it must be named without one: its command
        and args can carry secrets.
        """
        from ddtrace.llmobs._integrations.pydantic_ai import PydanticAIIntegration

        class FakeMCPServer:
            def __init__(self, server_id=None, url=None):
                self.id = server_id
                self.url = url

        class NotAnMCPServer:
            pass

        agent = mock.Mock()
        agent._user_toolsets = [
            FakeMCPServer(server_id="billing", url="https://user:sk-secret@mcp.example.com:8443/sse?token=abc"),
            FakeMCPServer(server_id=None, url=None),
            NotAnMCPServer(),
        ]
        integration = PydanticAIIntegration(integration_config=mock.Mock())

        with mock.patch.object(PydanticAIIntegration, "_mcp_server_classes", staticmethod(lambda: (FakeMCPServer,))):
            servers = integration._get_mcp_servers(agent)

        assert servers == [
            {"name": "billing", "uri": "https://mcp.example.com:8443"},
            {"name": "FakeMCPServer"},
        ], "the non-MCP toolset is filtered out, credentials and path are scrubbed, a urlless server keeps only a name"

    async def test_non_string_agent_name_falls_back_to_the_placeholder(
        self, pydantic_ai, pydantic_ai_llmobs, test_spans
    ):
        """A name that is not a str must not be printed onto the wire.

        The name is not type-checked by pydantic-ai, and the span encoder reprs a value it cannot
        encode, so an object here discloses its contents. Same rule as the tool description.
        """

        class Leaky:
            def __repr__(self):
                return "Leaky(token=sk-not-a-real-key)"

        agents = [pydantic_ai.Agent(model=_test_model(), name=Leaky())]
        await agents[0].run("Hello, world!")
        manifest = _manifest_of(test_spans.pop_traces()[0][0])

        assert "sk-not-a-real-key" not in safe_json(manifest)
        assert manifest["name"] == "PydanticAI Agent"

    async def test_non_string_tool_description_is_dropped(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """A tool description that is not a str must not be printed onto the wire.

        pydantic-ai accepts Tool(fn, description=<object>) and nothing downstream coerces it, and the
        span encoder falls back to repr for a value it cannot encode. A repr can carry credentials.
        """

        class SecretHolder:
            def __init__(self):
                self.api_key = "sk-not-a-real-key"

            def __repr__(self):
                return "SecretHolder(api_key={!r})".format(self.api_key)

        def mytool(x: str) -> str:
            """real docstring"""
            return "ok"

        _, manifest = await self._run(
            pydantic_ai,
            test_spans,
            name="test_agent",
            tools=[pydantic_ai.Tool(mytool, description=SecretHolder())],
            model=_test_model(),
        )

        assert "sk-not-a-real-key" not in safe_json(manifest)
        assert "description" not in manifest["tools"][0]

    async def test_non_string_tool_parameter_key_cannot_drop_the_payload(
        self, pydantic_ai, pydantic_ai_llmobs, test_spans
    ):
        """A non-str parameter key is coerced rather than left to break the encoder.

        Tool.from_schema takes a caller-supplied json_schema, so a non-str key reaches the manifest.
        The encoder sorts keys, comparing an int to a str raises, and it then returns None, which
        drops the whole batched payload rather than this one span.
        """

        def mytool(**kwargs) -> str:
            """Takes whatever the declared schema names, since the model does call it."""
            return "ok"

        tool = pydantic_ai.Tool.from_schema(
            mytool,
            name="schema_tool",
            description="d",
            json_schema={"type": "object", "properties": {"alpha": {"type": "string"}, 7: {"type": "string"}}},
        )

        _, manifest = await self._run(pydantic_ai, test_spans, name="test_agent", tools=[tool], model=_test_model())

        parameters = manifest["tools"][0]["parameters"]
        assert set(parameters) == {"alpha", "7"}, "both parameters survive, with keys coerced to str"
        assert safe_json(manifest) is not None, "the payload must still encode"

    async def test_non_finite_agent_settings_never_ship(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """The same rule for agent_settings, which does not go through the value coercer.

        float("inf") is a plausible way to say "no limit", and it reached the wire as a bare Infinity
        token. Python's json parser accepts that, a strict one does not, and spans ship batched, so a
        single such agent can invalidate a whole payload.

        Driven through output_retries rather than tool_timeout: pydantic-ai accepts a non-finite for
        both, but tool_timeout does not exist below 1.63.0, and guarding on it would skip this test on
        the versions where the hazard is just as reachable.
        """
        _, manifest = await self._run(
            pydantic_ai, test_spans, name="test_agent", output_retries=float("inf"), model=_test_model()
        )

        json.dumps(manifest, allow_nan=False)
        assert "retries" not in manifest["agent_settings"], "a non-finite retry budget must drop"

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="pydantic-ai < 1.63.0 has no agent metadata")
    async def test_cyclic_metadata_does_not_cost_the_section(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """A self-referential value terminates instead of recursing until the interpreter gives up."""
        cyclic: dict = {"team": "cx"}
        cyclic["self"] = cyclic

        _, manifest = await self._run(pydantic_ai, test_spans, name="test_agent", metadata=cyclic)

        json.dumps(manifest)
        assert manifest["name"] == "test_agent"

    async def test_generic_output_type_keeps_the_contract(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """A parameterized generic has no __name__, so a naive read raises and costs the contract.

        The schema assertion is the point: a truthiness check on name passed while the schema was
        silently dropped, so a field change inside Row could not move the recorded contract.
        """

        class Row(BaseModel):
            value: int

        _, manifest = await self._run(
            pydantic_ai, test_spans, name="test_agent", output_type=list[Row], model=_test_model()
        )

        output = manifest["data_contracts"]["output"]
        assert output["name"]
        assert output["schema"], "a container output must still carry the member model's schema"
        # Row's own field has to be reachable, whether inlined or behind the adapter's $ref/$defs.
        assert "value" in json.dumps(output["schema"])

    async def test_nested_generic_output_type_keeps_the_schema(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """A model nested two levels deep still counts, so the depth cap cannot silently exclude it."""

        class Row(BaseModel):
            value: int

        _, manifest = await self._run(
            pydantic_ai, test_spans, name="test_agent", output_type=dict[str, list[Row]], model=_test_model()
        )

        output = manifest["data_contracts"]["output"]
        assert output["schema"], "a nested container output must still carry the member model's schema"
        assert "value" in json.dumps(output["schema"])

    async def test_output_schema_keeps_json_nulls(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """A null inside a declared schema is data, not absence, so it round-trips verbatim.

        An enum of ["a", "b", null] previously lost the whole list, recording the field as
        unconstrained, which is a stable wrong contract rather than a missing one.
        """

        class Choice(BaseModel):
            choice: Literal["a", "b", None]
            maybe: Optional[str] = None

        _, manifest = await self._run(
            pydantic_ai, test_spans, name="test_agent", output_type=Choice, model=_test_model()
        )

        schema = manifest["data_contracts"]["output"]["schema"]
        assert schema == Choice.model_json_schema(), "the declared schema must round-trip unchanged"
        assert schema["properties"]["choice"]["enum"] == ["a", "b", None]
        assert schema["properties"]["maybe"]["default"] is None

    async def test_callable_metadata_emits_no_metadata(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """A metadata resolver is not captured, and is never called to find out what it returns.

        metadata is caller labels rather than behaviour and nothing versions on it, so the resolver's
        identity is not worth a key the shared schema does not define. What matters here is the
        negative: building a manifest must not execute caller code.
        """
        if PYDANTIC_AI_VERSION < (1, 39, 0):
            pytest.skip("pydantic-ai < 1.39.0 does not accept a callable metadata")

        called = []

        def tenant_metadata(ctx=None):
            """Compute metadata for the current run."""
            called.append(True)
            return {"tier": "gold"}

        agent = pydantic_ai.Agent(model=_test_model(), name="test_agent", metadata=tenant_metadata)
        # Built directly rather than through a run: pydantic-ai resolves metadata itself during a run,
        # so a run-scoped call counter cannot tell its calls apart from ours.
        manifest = pydantic_ai._datadog_integration._build_agent_manifest(agent)

        assert "metadata" not in manifest
        assert not called, "building the manifest must not evaluate a caller-supplied resolver"

    async def test_tool_preparation_is_recorded_as_a_capability(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """A prepare hook rewrites the tool list per step, so a change to it must move the manifest.

        This records that a transformation exists. It does NOT make the tool list correct: the tools
        key still reports what was declared, including a tool the hook removes.
        """

        async def drop_destructive(ctx, defs):
            """Withhold the destructive tool."""
            return [d for d in defs if d.name != "delete_account"]

        _, manifest = await self._run(
            pydantic_ai, test_spans, name="test_agent", prepare_tools=drop_destructive, model=_test_model()
        )

        prep = [cap for cap in manifest["capabilities"] if cap.get("type") == "tool_preparation"]
        assert len(prep) == 1
        assert prep[0]["name"] == "drop_destructive"
        assert prep[0]["content"]["source_hash"]

    async def test_scalar_output_type_carries_no_schema(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """Widening the gate must not start emitting a schema for a plain str output."""

        _, manifest = await self._run(pydantic_ai, test_spans, name="test_agent", output_type=str, model=_test_model())

        assert "schema" not in manifest["data_contracts"]["output"]

    async def test_section_failure_is_isolated(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """One section raising costs that section only, not the whole manifest.

        This is what keeps an unexpected framework change from blanking a customer's manifest entirely.
        """
        from ddtrace.llmobs._integrations.pydantic_ai import PydanticAIIntegration

        with mock.patch.object(PydanticAIIntegration, "_manifest_model", side_effect=ValueError("boom")):
            _, manifest = await self._run(pydantic_ai, test_spans, name="test_agent", instructions="Stay terse.")

        assert "model" not in manifest
        assert "model_settings" not in manifest
        assert manifest["name"] == "test_agent"
        assert manifest["instructions"] == "Stay terse."


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Credentials, path and query are all dropped; only the authority survives.
        ("https://user:sk-secret@mcp.example.com:8443/path?token=abc", "https://mcp.example.com:8443"),
        ("http://mcp.internal/sse", "http://mcp.internal"),
        # A scheme-less host:port still resolves, and defaults to https rather than to nothing.
        ("mcp.example.com:9000", "https://mcp.example.com:9000"),
        # An IPv6 literal is re-bracketed so the rebuilt authority stays parseable.
        ("http://[2001:db8::1]:8080/x", "http://[2001:db8::1]:8080"),
        # A non-HTTP scheme is not a scrubbable MCP URL, which is what keeps a stdio command out of
        # the manifest even if one ever reaches this helper.
        ("file:///usr/local/bin/secret-tool", None),
        ("stdio://run?key=sk-secret", None),
        # Single-slash forms have no "//" for urlsplit to find an authority in, so they take the
        # scheme-less host:port branch. Without a scheme check there they were rebuilt into an https
        # URL that never existed: "stdio:/usr/bin/srv" became "https://stdio".
        ("file:/usr/local/bin/secret-tool", None),
        ("stdio:/usr/local/bin/srv --api-key sk-secret", None),
        ("mailto:someone@example.com", None),
        # A stdio command line is not a URL. Its first token was previously rebuilt into a host, and
        # an "@" in a package name made the rest of the command read as userinfo.
        ("npx -y @modelcontextprotocol/server-filesystem /home/me", None),
        ("", None),
        (None, None),
        # No host means nothing worth emitting.
        ("https://", None),
        # A token containing "/" ends the authority early, so urlsplit reports the token itself as the
        # host. Emitting that would leak a credential fragment, so the whole value drops.
        ("https://TOKEN_abc123/@mcp.internal.corp/sse", None),
        ("https://abc123/@mcp.internal.corp/sse", None),
        # Not a host at all: a delimiter-free string must not be echoed back as one.
        ("\\\\host\\share", None),
        # urlsplit itself raises here, not just its hostname property: this netloc NFKC-normalizes
        # into a delimiter. A raise would have cost the whole capability grouping.
        ("https://mcp.corp：8080/sse", None),
        # A bad port is the other urlsplit raise.
        ("https://mcp.example.com:notaport/sse", None),
        # An @ that genuinely is userinfo parses correctly and the credential is stripped.
        ("https://user:pw@mcp.example.com/sse", "https://mcp.example.com"),
    ],
)
def test_redact_mcp_uri(raw, expected):
    """An allowlist: only scheme, host and port survive, so a new URL component drops by default.

    Runs on every venv, unlike the toolset-level test, because it needs no MCP install.
    """
    from ddtrace.llmobs._integrations.pydantic_ai import _redact_mcp_uri

    assert _redact_mcp_uri(raw) == expected


def test_redact_mcp_uri_drops_userinfo_that_looks_like_a_host():
    """A credential in the userinfo must never be mistaken for the host it precedes."""
    from ddtrace.llmobs._integrations.pydantic_ai import _redact_mcp_uri

    scrubbed = _redact_mcp_uri("https://sk-secret:sk-secret@real-host.example.com/x")
    assert scrubbed == "https://real-host.example.com"
    assert "sk-secret" not in scrubbed
