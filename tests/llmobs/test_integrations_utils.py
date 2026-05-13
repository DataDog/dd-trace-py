from ddtrace.llmobs._integrations.utils import _extract_chat_template_from_instructions
from ddtrace.llmobs._integrations.utils import _normalize_prompt_variables
from ddtrace.llmobs._integrations.utils import _openai_parse_input_response_messages


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
