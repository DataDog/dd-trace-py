from types import SimpleNamespace

import pytest

from ddtrace.llmobs._integrations.utils import _extract_chat_template_from_instructions
from ddtrace.llmobs._integrations.utils import _normalize_prompt_variables
from ddtrace.llmobs._integrations.utils import _openai_parse_input_response_messages
from ddtrace.llmobs._integrations.utils import openai_construct_message_from_streamed_chunks


def test_basic_functionality():
    """Test basic variable replacement with multiple instructions and roles."""
    instructions = [
        {
            "role": "developer",
            "content": [{"text": "Be helpful"}],
        },
        {
            "role": "user",
            "content": [{"text": "Hello John, your email is john@example.com"}],
        },
    ]
    variables = {
        "name": "John",
        "email": "john@example.com",
    }

    result = _extract_chat_template_from_instructions(instructions, variables)

    assert len(result) == 2
    assert result[0]["role"] == "developer"
    assert result[0]["content"] == "Be helpful"
    assert result[1]["role"] == "user"
    assert result[1]["content"] == "Hello {{name}}, your email is {{email}}"


def test_overlapping_values_and_partial_matches():
    """Test longest-first matching for overlaps and partial word matches."""
    # Test 1: Overlapping values - longest should win
    instructions = [
        {
            "role": "user",
            "content": [{"text": "The phrase is: AI is cool"}],
        }
    ]
    variables = {"short": "AI", "long": "AI is cool"}
    result = _extract_chat_template_from_instructions(instructions, variables)
    assert result[0]["content"] == "The phrase is: {{long}}"

    # Test 2: Partial word matches should work (e.g., "test" inside "testing")
    instructions = [
        {
            "role": "user",
            "content": [{"text": "We are testing the feature"}],
        }
    ]
    variables = {"action": "test"}
    result = _extract_chat_template_from_instructions(instructions, variables)
    assert result[0]["content"] == "We are {{action}}ing the feature"


def test_special_characters_and_escaping():
    """Test that special characters are handled correctly."""
    instructions = [
        {
            "role": "user",
            "content": [{"text": "The price is $99.99 (plus $5.00 tax)"}],
        }
    ]
    variables = {"price": "$99.99", "tax": "$5.00"}

    result = _extract_chat_template_from_instructions(instructions, variables)

    assert result[0]["content"] == "The price is {{price}} (plus {{tax}} tax)"


def test_empty_and_edge_cases():
    """Test empty variables, empty values, and malformed instructions."""
    # Empty variables dict
    instructions = [{"role": "user", "content": [{"text": "No variables"}]}]
    result = _extract_chat_template_from_instructions(instructions, {})
    assert result[0]["content"] == "No variables"

    # Empty variable values are skipped
    instructions = [{"role": "user", "content": [{"text": "Hello world"}]}]
    result = _extract_chat_template_from_instructions(instructions, {"empty": "", "greeting": "Hello"})
    assert result[0]["content"] == "{{greeting}} world"

    # Instructions without role or content are skipped
    instructions = [
        {"content": [{"text": "No role"}]},
        {"role": "developer", "content": []},
        {"role": "user", "content": [{"text": "Valid"}]},
    ]
    result = _extract_chat_template_from_instructions(instructions, {})
    assert len(result) == 1
    assert result[0]["role"] == "user"


def test_response_input_text_objects():
    """Test handling of ResponseInputText objects with .text attribute."""

    class ResponseInputText:
        def __init__(self, text):
            self.text = text

    instructions = [
        {
            "role": "user",
            "content": [
                {"text": "Part one "},
                {"text": "Question: What is AI?"},
            ],
        }
    ]
    variables = {"question": ResponseInputText("What is AI?")}

    # Normalize variables before extraction (as done in openai_set_meta_tags_from_response)
    normalized_vars = _normalize_prompt_variables(variables)
    result = _extract_chat_template_from_instructions(instructions, normalized_vars)

    # Also tests that multiple content items are concatenated
    assert result[0]["content"] == "Part one Question: {{question}}"


def test_normalize_prompt_variables():
    """Test normalization of complex variable types."""

    class ResponseInputText:
        def __init__(self, text):
            self.text = text

    class ResponseInputImage:
        def __init__(self, image_url=None, file_id=None):
            self.type = "input_image"
            self.image_url = image_url
            self.file_id = file_id

    class ResponseInputFile:
        def __init__(self, file_url=None, file_id=None, filename=None, file_data=None):
            self.type = "input_file"
            self.file_url = file_url
            self.file_id = file_id
            self.filename = filename
            self.file_data = file_data

    variables = {
        "plain_string": "hello",
        "text_obj": ResponseInputText("world"),
        "image_url": ResponseInputImage(image_url="https://example.com/img.png"),
        "image_file": ResponseInputImage(file_id="file-123"),
        "image_fallback": ResponseInputImage(),
        "file_url": ResponseInputFile(file_url="https://example.com/doc.pdf"),
        "file_name": ResponseInputFile(filename="report.pdf"),
        "file_data": ResponseInputFile(file_data="Some content"),
        "file_fallback": ResponseInputFile(),
    }

    result = _normalize_prompt_variables(variables)

    assert result["plain_string"] == "hello"
    assert result["text_obj"] == "world"
    assert result["image_url"] == "https://example.com/img.png"
    assert result["image_file"] == "file-123"
    assert result["image_fallback"] == "[image]"
    assert result["file_url"] == "https://example.com/doc.pdf"
    assert result["file_name"] == "report.pdf"
    assert result["file_data"] == "[file]"
    assert result["file_fallback"] == "[file]"


def test_extract_chat_template_with_falsy_values():
    """Test that falsy but valid values (0, False) are preserved in template extraction."""

    instructions = [
        {
            "role": "user",
            "content": [
                {"text": "Count: 0, Flag: False, Empty: "},
            ],
        }
    ]
    variables = {"count": 0, "flag": False, "empty": ""}

    result = _extract_chat_template_from_instructions(instructions, variables)

    # 0 and False should be replaced with placeholders
    # Empty string should remain as-is (not replaceable through reverse-templating)
    assert result[0]["content"] == "Count: {{count}}, Flag: {{flag}}, Empty: "


class TestOpenAIParseInputResponseMessages:
    """Tests for _openai_parse_input_response_messages with both dict and SDK object inputs."""

    def test_dict_regular_message(self):
        messages = [{"role": "user", "content": "Hello"}]
        processed, tool_call_ids = _openai_parse_input_response_messages(messages)
        assert len(processed) == 1
        assert processed[0]["role"] == "user"
        assert processed[0]["content"] == "Hello"
        assert tool_call_ids == []

    def test_dict_function_call(self):
        messages = [
            {
                "type": "function_call",
                "call_id": "call_abc",
                "name": "get_weather",
                "arguments": '{"location": "SF"}',
            }
        ]
        processed, tool_call_ids = _openai_parse_input_response_messages(messages)
        assert len(processed) == 1
        assert processed[0]["role"] == "assistant"
        tc = processed[0]["tool_calls"][0]
        assert tc["tool_id"] == "call_abc"
        assert tc["name"] == "get_weather"
        assert tc["arguments"] == {"location": "SF"}
        assert tc["type"] == "function_call"

    def test_dict_function_call_output(self):
        messages = [
            {
                "type": "function_call_output",
                "call_id": "call_abc",
                "output": '{"temp": "72F"}',
            }
        ]
        processed, tool_call_ids = _openai_parse_input_response_messages(messages)
        assert len(processed) == 1
        assert processed[0]["role"] == "user"
        tr = processed[0]["tool_results"][0]
        assert tr["tool_id"] == "call_abc"
        assert tr["result"] == '{"temp": "72F"}'
        assert tool_call_ids == ["call_abc"]

    def test_sdk_object_function_call(self):
        """SDK objects (e.g. ResponseFunctionToolCall) must be handled via _get_attr, not dict access."""

        class FakeResponseFunctionToolCall:
            type = "function_call"
            call_id = "call_sdk_123"
            name = "search"
            arguments = '{"query": "python"}'

        messages = [FakeResponseFunctionToolCall()]
        processed, tool_call_ids = _openai_parse_input_response_messages(messages)
        assert len(processed) == 1
        assert processed[0]["role"] == "assistant"
        tc = processed[0]["tool_calls"][0]
        assert tc["tool_id"] == "call_sdk_123"
        assert tc["name"] == "search"
        assert tc["arguments"] == {"query": "python"}
        assert tc["type"] == "function_call"
        assert tool_call_ids == []

    def test_sdk_object_function_call_output(self):
        """SDK objects representing function call output must be parsed correctly."""

        class FakeFunctionCallOutput:
            type = "function_call_output"
            call_id = "call_sdk_456"
            output = '{"result": "42"}'
            name = "calculate"

        messages = [FakeFunctionCallOutput()]
        processed, tool_call_ids = _openai_parse_input_response_messages(messages)
        assert len(processed) == 1
        assert processed[0]["role"] == "user"
        tr = processed[0]["tool_results"][0]
        assert tr["tool_id"] == "call_sdk_456"
        assert tr["result"] == '{"result": "42"}'
        assert tool_call_ids == ["call_sdk_456"]

    def test_mixed_dict_and_sdk_objects(self):
        """A list mixing dicts and SDK objects should all be parsed correctly."""

        class FakeResponseFunctionToolCall:
            type = "function_call"
            call_id = "call_mixed_1"
            name = "get_weather"
            arguments = '{"location": "NYC"}'

        class FakeFunctionCallOutput:
            type = "function_call_output"
            call_id = "call_mixed_1"
            output = "sunny"
            name = "get_weather"

        messages = [
            {"role": "user", "content": "What's the weather?"},
            FakeResponseFunctionToolCall(),
            FakeFunctionCallOutput(),
        ]
        processed, tool_call_ids = _openai_parse_input_response_messages(messages)
        assert len(processed) == 3
        assert processed[0]["role"] == "user"
        assert processed[0]["content"] == "What's the weather?"
        assert processed[1]["role"] == "assistant"
        assert processed[1]["tool_calls"][0]["tool_id"] == "call_mixed_1"
        assert processed[2]["role"] == "user"
        assert processed[2]["tool_results"][0]["tool_id"] == "call_mixed_1"
        assert tool_call_ids == ["call_mixed_1"]

    def test_function_call_output_list_output(self):
        """output as a list: only input_text parts are captured; images/files are skipped."""

        class TextPart:
            type = "input_text"
            text = "42 degrees"

        class ImagePart:
            type = "input_image"
            image_url = "https://example.com/img.png"

        messages = [
            {
                "type": "function_call_output",
                "call_id": "call_list",
                "output": [TextPart(), ImagePart()],
            }
        ]
        processed, tool_call_ids = _openai_parse_input_response_messages(messages)
        assert len(processed) == 1
        assert processed[0]["role"] == "user"
        tr = processed[0]["tool_results"][0]
        assert tr["tool_id"] == "call_list"
        assert tr["result"] == "42 degrees"
        assert tool_call_ids == ["call_list"]

    def test_sdk_reasoning_item_skipped(self):
        """ResponseReasoningItem (type='reasoning') should be skipped silently."""

        class FakeResponseReasoningItem:
            type = "reasoning"
            id = "reasoning_1"
            summary = []

        messages = [
            {"role": "user", "content": "Think about this"},
            FakeResponseReasoningItem(),
        ]
        processed, tool_call_ids = _openai_parse_input_response_messages(messages)
        assert len(processed) == 1
        assert processed[0]["role"] == "user"
        assert tool_call_ids == []


def _chunk(content=None, reasoning_content=None, role=None, finish_reason=None):
    delta = SimpleNamespace(content=content, reasoning_content=reasoning_content, role=role)
    return SimpleNamespace(delta=delta, finish_reason=finish_reason, usage=None, index=0)


class TestOpenAIConstructMessageFromStreamedChunks:
    def test_reasoning_then_content_chunks_aggregate_both(self):
        # OpenAI-compatible reasoning providers (DeepSeek, Qwen, etc.) typically emit
        # reasoning_content chunks first, then content chunks.
        chunks = [
            _chunk(role="assistant"),
            _chunk(reasoning_content="Let me "),
            _chunk(reasoning_content="think..."),
            _chunk(content="The answer "),
            _chunk(content="is 391."),
            _chunk(finish_reason="stop"),
        ]
        message = openai_construct_message_from_streamed_chunks(chunks)
        assert message["reasoning_content"] == "Let me think..."
        assert message["content"] == "The answer is 391."
        assert message["role"] == "assistant"
        assert message["finish_reason"] == "stop"

    def test_reasoning_only_stream(self):
        chunks = [
            _chunk(role="assistant"),
            _chunk(reasoning_content="hmm"),
        ]
        message = openai_construct_message_from_streamed_chunks(chunks)
        assert message["reasoning_content"] == "hmm"
        assert message["content"] == ""

    def test_no_reasoning_key_when_absent(self):
        chunks = [_chunk(role="assistant"), _chunk(content="hello")]
        message = openai_construct_message_from_streamed_chunks(chunks)
        assert "reasoning_content" not in message
        assert message["content"] == "hello"

    def test_interleaved_reasoning_and_content_in_same_chunk(self):
        chunks = [
            _chunk(role="assistant"),
            _chunk(reasoning_content="r", content="c"),
        ]
        message = openai_construct_message_from_streamed_chunks(chunks)
        assert message["reasoning_content"] == "r"
        assert message["content"] == "c"


# -- MLOB-7584: Context Visualization shared helpers ----------------------------


class TestSplitTokensByChars:
    def test_distributes_proportionally(self):
        from ddtrace.llmobs._integrations.utils import split_tokens_by_chars

        # 800 chars total; cat A = 200, B = 600. Split of 1000 tokens: A=250, B=750.
        result = split_tokens_by_chars(1000, {"A": 200, "B": 600})
        assert result == {"A": 250, "B": 750}

    def test_zero_total_tokens_returns_zeros(self):
        from ddtrace.llmobs._integrations.utils import split_tokens_by_chars

        result = split_tokens_by_chars(0, {"A": 100, "B": 200})
        assert result == {"A": 0, "B": 0}

    def test_zero_total_chars_returns_zeros(self):
        from ddtrace.llmobs._integrations.utils import split_tokens_by_chars

        # All categories empty — proportional split is undefined; return zeros.
        result = split_tokens_by_chars(1000, {"A": 0, "B": 0})
        assert result == {"A": 0, "B": 0}

    def test_negative_total_tokens_returns_zeros(self):
        from ddtrace.llmobs._integrations.utils import split_tokens_by_chars

        # Defensive: negative or malformed totals shouldn't blow up the visualization.
        result = split_tokens_by_chars(-5, {"A": 100})
        assert result == {"A": 0}

    def test_empty_categories_returns_empty(self):
        from ddtrace.llmobs._integrations.utils import split_tokens_by_chars

        assert split_tokens_by_chars(1000, {}) == {}


class TestSectionsWithPct:
    def test_builds_sections_with_rounded_pct(self):
        from ddtrace.llmobs._integrations.utils import _sections_with_pct

        result = _sections_with_pct({"system": 200, "tools": 800})
        assert result == [
            {"name": "system", "tokens": 200, "pct": 20.0},
            {"name": "tools", "tokens": 800, "pct": 80.0},
        ]

    def test_omits_zero_token_categories(self):
        from ddtrace.llmobs._integrations.utils import _sections_with_pct

        # Empty categories shouldn't render empty segments in the UI bar — drop them here.
        result = _sections_with_pct({"system": 100, "tools": 0, "user_messages": 100})
        names = [s["name"] for s in result]
        assert names == ["system", "user_messages"]

    def test_empty_input_returns_empty(self):
        from ddtrace.llmobs._integrations.utils import _sections_with_pct

        assert _sections_with_pct({}) == []
        assert _sections_with_pct({"tools": 0}) == []


class TestTagContextDelta:
    """Direct tests on the helper using a real Span — exercise the emission path end-to-end."""

    def _make_span(self):
        # Use the LLMObs test harness span fixture. Mirrors patterns in tests/llmobs/test_llmobs.py.
        from ddtrace._trace.span import Span as DDSpan

        return DDSpan(name="test_agent")

    def test_emits_when_either_first_or_last_tokens_present(self):
        from ddtrace.llmobs._integrations.utils import tag_context_delta
        from ddtrace.llmobs._utils import _get_llmobs_data_metastruct

        span = self._make_span()
        tag_context_delta(
            span,
            first_token_counts={"system": 100, "tools": 200, "user_messages": 50, "assistant_messages": 0},
            last_token_counts={"system": 100, "tools": 200, "user_messages": 100, "assistant_messages": 200},
            first_input_tokens=350,
            last_input_tokens=600,
            context_window_size=128_000,
        )

        meta = _get_llmobs_data_metastruct(span).get("meta", {})
        delta = meta.get("metadata", {}).get("_dd", {}).get("context_delta")
        assert delta is not None
        assert delta["first_input_tokens"] == 350
        assert delta["last_input_tokens"] == 600
        assert delta["delta_tokens"] == 250
        assert delta["context_window_size"] == 128_000
        # 350/128000 ≈ 0.27, rounded to 0.3.
        assert delta["first_usage_pct"] == 0.3
        # 600/128000 ≈ 0.469, rounded to 0.5.
        assert delta["last_usage_pct"] == 0.5
        # assistant_messages == 0 in first should be dropped from first_sections.
        first_section_names = [s["name"] for s in delta["first_sections"]]
        assert "assistant_messages" not in first_section_names
        last_section_names = [s["name"] for s in delta["last_sections"]]
        assert last_section_names == ["system", "tools", "user_messages", "assistant_messages"]

    def test_skips_emission_when_both_tokens_zero(self):
        from ddtrace.llmobs._integrations.utils import tag_context_delta
        from ddtrace.llmobs._utils import _get_llmobs_data_metastruct

        span = self._make_span()
        tag_context_delta(
            span,
            first_token_counts={},
            last_token_counts={},
            first_input_tokens=0,
            last_input_tokens=0,
            context_window_size=128_000,
        )

        meta = _get_llmobs_data_metastruct(span).get("meta", {})
        # No context_delta key should have been set.
        assert "context_delta" not in meta.get("metadata", {}).get("_dd", {})

    def test_unknown_context_window_emits_zero_pct(self):
        from ddtrace.llmobs._integrations.utils import tag_context_delta
        from ddtrace.llmobs._utils import _get_llmobs_data_metastruct

        span = self._make_span()
        tag_context_delta(
            span,
            first_token_counts={"system": 100},
            last_token_counts={"system": 100},
            first_input_tokens=100,
            last_input_tokens=100,
            context_window_size=0,  # unknown model
        )

        meta = _get_llmobs_data_metastruct(span).get("meta", {})
        delta = meta["metadata"]["_dd"]["context_delta"]
        assert delta["context_window_size"] == 0
        assert delta["first_usage_pct"] == 0.0
        assert delta["last_usage_pct"] == 0.0

    def test_omits_section_lists_when_all_zero(self):
        from ddtrace.llmobs._integrations.utils import tag_context_delta
        from ddtrace.llmobs._utils import _get_llmobs_data_metastruct

        span = self._make_span()
        tag_context_delta(
            span,
            first_token_counts={"system": 0, "tools": 0},
            last_token_counts={"system": 100},
            first_input_tokens=100,  # nonzero so emission isn't skipped
            last_input_tokens=100,
            context_window_size=128_000,
        )
        meta = _get_llmobs_data_metastruct(span).get("meta", {})
        delta = meta["metadata"]["_dd"]["context_delta"]
        # first_sections has all-zero categories; should be entirely omitted.
        assert "first_sections" not in delta
        # last_sections is non-empty; should be present.
        assert delta["last_sections"] == [{"name": "system", "tokens": 100, "pct": 100.0}]


# -- MLOB-7584: OpenAIAgentsIntegration adapter methods + helpers --------------


class _MinimalIntegrationConfig:
    """Minimal stub for BaseLLMIntegration's integration_config requirement."""

    distributed_tracing = False
    service = ""
    metrics_enabled = False
    logs_enabled = False
    log_prompt_completion_sample_rate = 0.0
    span_prompt_completion_sample_rate = 0.0


def _make_integration():
    """Build an OpenAIAgentsIntegration.

    Tests that exercise ``record_llm_side`` / ``record_agent_side`` / ``emit_context_delta``
    must enable LLMObs via the ``_enable_llmobs`` autouse fixture on
    ``TestOpenAIAgentsContextState`` — ``llmobs_enabled`` is a property reading the global
    ``LLMObs.enabled`` flag.
    """
    from ddtrace.llmobs._integrations.openai_agents import OpenAIAgentsIntegration

    return OpenAIAgentsIntegration(integration_config=_MinimalIntegrationConfig())


class TestOpenAIAgentsContextWindow:
    """Cover the model -> context_window resolver, including the o1-preview prefix bug."""

    def test_exact_match(self):
        integration = _make_integration()
        assert integration._context_window_for("gpt-4o") == 128_000
        assert integration._context_window_for("gpt-4") == 8_192
        assert integration._context_window_for("o1") == 200_000

    def test_dated_prefix_match(self):
        # gpt-4o-2024-08-06 -> gpt-4o (longest-prefix wins)
        integration = _make_integration()
        assert integration._context_window_for("gpt-4o-2024-08-06") == 128_000
        assert integration._context_window_for("gpt-4o-mini-2024-07-18") == 128_000

    def test_o1_preview_resolves_to_128k_not_200k(self):
        # Regression: longest-prefix sort must pick "o1-preview" (128k) over "o1" (200k).
        integration = _make_integration()
        assert integration._context_window_for("o1-preview") == 128_000
        assert integration._context_window_for("o1-preview-2024-09-12") == 128_000

    def test_o1_mini_not_confused_with_o1(self):
        integration = _make_integration()
        assert integration._context_window_for("o1-mini") == 128_000
        assert integration._context_window_for("o1-mini-2024-09-12") == 128_000

    def test_gpt_4_mini_not_confused_with_gpt_4(self):
        integration = _make_integration()
        # gpt-4o-mini must match the 128k entry, not gpt-4 (8k).
        assert integration._context_window_for("gpt-4o-mini") == 128_000

    def test_unknown_model_returns_zero(self):
        integration = _make_integration()
        assert integration._context_window_for("some-unknown-model") == 0
        assert integration._context_window_for("") == 0
        assert integration._context_window_for(None) == 0  # defensive


class TestOpenAIAgentsContextState:
    """Cover the per-trace snapshot lifecycle: record -> emit -> pop."""

    @pytest.fixture(autouse=True)
    def _enable_llmobs(self):
        """``record_*`` methods short-circuit when LLMObs is disabled. Flip the global
        flag for each test, then restore.
        """
        from ddtrace.llmobs import LLMObs

        prior = LLMObs.enabled
        LLMObs.enabled = True
        try:
            yield
        finally:
            LLMObs.enabled = prior

    def _make_span(self):
        from ddtrace._trace.span import Span as DDSpan

        return DDSpan(name="agent_root")

    def test_first_call_locks_first_llm_subsequent_overwrites_last(self):
        integration = _make_integration()
        integration.record_llm_side(
            trace_id=1,
            input_tokens=100,
            system_chars=20,
            user_chars=50,
            assistant_chars=30,
            model="gpt-4o",
        )
        integration.record_llm_side(
            trace_id=1,
            input_tokens=500,
            system_chars=20,
            user_chars=80,
            assistant_chars=400,
            model="gpt-4o",
        )

        state = integration._context_state[1]
        assert state["first_llm"]["input_tokens"] == 100
        assert state["last_llm"]["input_tokens"] == 500

    def test_record_agent_side_locks_first_overwrites_last(self):
        integration = _make_integration()
        integration.record_agent_side(trace_id=2, tools_chars=300)
        integration.record_agent_side(trace_id=2, tools_chars=500)

        state = integration._context_state[2]
        assert state["first_agent"]["tools_chars"] == 300
        assert state["last_agent"]["tools_chars"] == 500

    def test_record_skips_when_llmobs_disabled(self):
        # Override the autouse-enabled flag for this single test.
        from ddtrace.llmobs import LLMObs

        LLMObs.enabled = False
        try:
            integration = _make_integration()
            integration.record_llm_side(
                trace_id=3,
                input_tokens=100,
                system_chars=0,
                user_chars=0,
                assistant_chars=0,
                model="",
            )
            integration.record_agent_side(trace_id=3, tools_chars=100)
            assert 3 not in integration._context_state
        finally:
            LLMObs.enabled = True  # restore for the rest of the test class

    def test_emit_assembles_and_pops_state(self):
        from ddtrace.llmobs._utils import _get_llmobs_data_metastruct

        integration = _make_integration()
        integration.record_llm_side(
            trace_id=4,
            input_tokens=1000,
            system_chars=100,
            user_chars=400,
            assistant_chars=500,
            model="gpt-4o",
        )
        integration.record_agent_side(trace_id=4, tools_chars=200)
        integration.record_llm_side(
            trace_id=4,
            input_tokens=2000,
            system_chars=100,
            user_chars=400,
            assistant_chars=1500,
            model="gpt-4o",
        )
        integration.record_agent_side(trace_id=4, tools_chars=200)

        span = self._make_span()
        integration.emit_context_delta(span, trace_id=4)

        # State was popped.
        assert 4 not in integration._context_state

        meta = _get_llmobs_data_metastruct(span).get("meta", {})
        delta = meta["metadata"]["_dd"]["context_delta"]
        assert delta["first_input_tokens"] == 1000
        assert delta["last_input_tokens"] == 2000
        assert delta["delta_tokens"] == 1000
        assert delta["context_window_size"] == 128_000  # from gpt-4o
        # All four generic categories should be present in first_sections and last_sections.
        first_names = {s["name"] for s in delta["first_sections"]}
        assert first_names == {"system", "tools", "user_messages", "assistant_messages"}
        last_names = {s["name"] for s in delta["last_sections"]}
        assert last_names == {"system", "tools", "user_messages", "assistant_messages"}

    def test_emit_skips_when_no_llm_snapshots(self):
        integration = _make_integration()
        # Only agent-side data — no LLM call happened.
        integration.record_agent_side(trace_id=5, tools_chars=200)

        span = self._make_span()
        integration.emit_context_delta(span, trace_id=5)

        from ddtrace.llmobs._utils import _get_llmobs_data_metastruct

        meta = _get_llmobs_data_metastruct(span).get("meta", {})
        assert "context_delta" not in meta.get("metadata", {}).get("_dd", {})
        # State still popped.
        assert 5 not in integration._context_state

    def test_emit_skips_when_no_state_for_trace(self):
        integration = _make_integration()
        span = self._make_span()
        # Never recorded anything for trace 999.
        integration.emit_context_delta(span, trace_id=999)

        from ddtrace.llmobs._utils import _get_llmobs_data_metastruct

        meta = _get_llmobs_data_metastruct(span).get("meta", {})
        assert "context_delta" not in meta.get("metadata", {}).get("_dd", {})

    def test_clear_state_clears_context_state(self):
        integration = _make_integration()
        integration.record_llm_side(
            trace_id=6, input_tokens=100, system_chars=0, user_chars=0, assistant_chars=0, model=""
        )
        assert 6 in integration._context_state
        integration.clear_state()
        assert integration._context_state == {}


class TestSplitMessageChars:
    """Cover the role-split helper that drives per-category char counts."""

    def test_string_input_is_bucketed_as_user(self):
        from ddtrace.llmobs._integrations.openai_agents import split_message_chars

        assert split_message_chars("hello world") == (11, 0)

    def test_empty_string(self):
        from ddtrace.llmobs._integrations.openai_agents import split_message_chars

        assert split_message_chars("") == (0, 0)

    def test_neither_string_nor_list_returns_zeros(self):
        from ddtrace.llmobs._integrations.openai_agents import split_message_chars

        assert split_message_chars(None) == (0, 0)
        assert split_message_chars(42) == (0, 0)
        assert split_message_chars({"role": "user", "content": "x"}) == (0, 0)

    def test_role_split_dicts(self):
        from ddtrace.llmobs._integrations.openai_agents import split_message_chars

        messages = [
            {"role": "user", "content": "hello"},  # 5
            {"role": "assistant", "content": "hi there"},  # 8
            {"role": "user", "content": "tell me a story"},  # 15
        ]
        assert split_message_chars(messages) == (20, 8)

    def test_role_split_objects(self):
        from types import SimpleNamespace

        from ddtrace.llmobs._integrations.openai_agents import split_message_chars

        messages = [
            SimpleNamespace(role="user", content="abc"),
            SimpleNamespace(role="assistant", content="defgh"),
        ]
        assert split_message_chars(messages) == (3, 5)

    def test_system_is_ignored_tool_is_bucketed_with_assistant(self):
        from ddtrace.llmobs._integrations.openai_agents import split_message_chars

        # System messages flow through response_system_instructions, not the message list,
        # so they are excluded here. Tool-result messages (role=tool) are injected into
        # response.input after each tool call by the agent loop; bucketing them with
        # role=assistant keeps total counted chars equal to total response.input chars,
        # which is what the proportional token split relies on.
        messages = [
            {"role": "system", "content": "you are a helpful agent"},  # 23 chars, excluded
            {"role": "user", "content": "hi"},  # 2 chars -> user
            {"role": "tool", "content": "result body"},  # 11 chars -> assistant
            {"role": "assistant", "content": "ok"},  # 2 chars -> assistant
        ]
        assert split_message_chars(messages) == (2, 13)

    def test_none_content_is_zero(self):
        from ddtrace.llmobs._integrations.openai_agents import split_message_chars

        messages = [{"role": "user", "content": None}, {"role": "assistant"}]
        assert split_message_chars(messages) == (0, 0)


class TestCountToolsChars:
    """Cover the tools+handoffs char counter."""

    def test_empty_agent_returns_zero(self):
        from types import SimpleNamespace

        from ddtrace.llmobs._integrations.openai_agents import count_tools_chars

        agent = SimpleNamespace()
        assert count_tools_chars(agent) == 0

    def test_none_attributes_return_zero(self):
        from types import SimpleNamespace

        from ddtrace.llmobs._integrations.openai_agents import count_tools_chars

        agent = SimpleNamespace(tools=None, handoffs=None)
        assert count_tools_chars(agent) == 0

    def test_tools_and_handoffs_counted_together(self):
        from types import SimpleNamespace

        from ddtrace.llmobs._integrations.openai_agents import count_tools_chars

        # Both serialize via safe_json or str fallback.
        agent = SimpleNamespace(tools=[{"name": "research"}], handoffs=[{"agent_name": "summary"}])
        chars = count_tools_chars(agent)
        # Both objects serialize to at least the dict's str form length.
        assert chars > 0
