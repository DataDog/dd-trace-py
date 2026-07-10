import base64
from types import SimpleNamespace

from ddtrace.ext import SpanTypes
from ddtrace.llmobs._integrations.utils import _extract_chat_template_from_instructions
from ddtrace.llmobs._integrations.utils import _extract_content_parts
from ddtrace.llmobs._integrations.utils import _normalize_prompt_variables
from ddtrace.llmobs._integrations.utils import _openai_parse_input_response_messages
from ddtrace.llmobs._integrations.utils import audio_mime_type_from_format
from ddtrace.llmobs._integrations.utils import format_audio_part
from ddtrace.llmobs._integrations.utils import openai_construct_message_from_streamed_chunks
from ddtrace.llmobs._integrations.utils import openai_set_meta_tags_from_chat
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import get_llmobs_input_messages


def test_format_audio_part_from_bytes():
    """Raw bytes are base64-encoded into an AudioPart with the given mime type."""
    raw = b"\x00\x01\x02\x03"
    part = format_audio_part(raw, "audio/wav")
    assert part == {"mime_type": "audio/wav", "content": base64.b64encode(raw).decode("utf-8")}


def test_format_audio_part_from_base64_string():
    """An already-encoded base64 string is passed through unchanged."""
    part = format_audio_part("AAECAw==", "audio/mp3")
    assert part == {"mime_type": "audio/mp3", "content": "AAECAw=="}


def test_audio_mime_type_from_format():
    """OpenAI audio formats map to MIME types, falling back to audio/<format>."""
    assert audio_mime_type_from_format("wav") == "audio/wav"
    assert audio_mime_type_from_format("mp3") == "audio/mpeg"
    assert audio_mime_type_from_format("FLAC") == "audio/flac"
    assert audio_mime_type_from_format("opus") == "audio/opus"
    assert audio_mime_type_from_format("  MP3 ") == "audio/mpeg"  # whitespace + case insensitive
    assert audio_mime_type_from_format("") == "audio/wav"


def test_extract_content_parts_collects_audio():
    """Captured input_audio becomes an AudioPart and leaves no '[audio]' text marker behind."""
    text, audio_parts = _extract_content_parts(
        [
            {"type": "text", "text": "what is said here?"},
            {"type": "input_audio", "input_audio": {"data": "AAECAw==", "format": "mp3"}},
        ]
    )
    assert text == "what is said here?"
    assert audio_parts == [{"mime_type": "audio/mpeg", "content": "AAECAw=="}]


def test_extract_content_parts_multiple_audio_only():
    """A message with only input_audio parts captures each as an AudioPart and has empty text."""
    text, audio_parts = _extract_content_parts(
        [
            {"type": "input_audio", "input_audio": {"data": "AAA=", "format": "wav"}},
            {"type": "input_audio", "input_audio": {"data": "BBB=", "format": "mp3"}},
        ]
    )
    assert text == ""
    assert audio_parts == [
        {"mime_type": "audio/wav", "content": "AAA="},
        {"mime_type": "audio/mpeg", "content": "BBB="},
    ]


def test_extract_content_parts_audio_marker_fallback_when_no_data():
    """When an input_audio part carries no data, fall back to the '[audio]' text marker."""
    text, audio_parts = _extract_content_parts(
        [
            {"type": "text", "text": "listen:"},
            {"type": "input_audio", "input_audio": {"format": "wav"}},
        ]
    )
    assert text == "listen:\n[audio]"
    assert audio_parts == []


def test_extract_content_parts_no_audio():
    """Text/image-only content yields no audio parts."""
    text, audio_parts = _extract_content_parts(
        [
            {"type": "text", "text": "hello"},
            {"type": "image_url", "image_url": "http://example.com/x.png"},
        ]
    )
    assert text == "hello\n[image]"
    assert audio_parts == []


def test_chat_streamed_output_does_not_leak_tool_results_into_input(tracer):
    """Regression: the streamed-output branch must use its own tool_results, not the input loop's.

    ReAct content in a streamed output previously appended to the last input message's tool_results
    list (the variable was discarded with ``_`` and a stale value leaked through), corrupting input.
    """
    react = "Action: search\nAction Input: weather\nObservation: {}"
    kwargs = {"messages": [{"role": "user", "content": react.format("from-input")}]}
    streamed_output = [{"role": "assistant", "content": react.format("from-output")}]
    with tracer.trace("openai.request", span_type=SpanTypes.LLM) as span:
        _annotate_llmobs_span_data(span, kind="llm")  # route input/output as messages, as the integration does
        openai_set_meta_tags_from_chat(span, kwargs, streamed_output)
        input_messages = get_llmobs_input_messages(span)

    assert len(input_messages) == 1
    tool_results = input_messages[0].get("tool_results", [])
    assert len(tool_results) == 1
    assert tool_results[0]["result"] == "from-input"


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
