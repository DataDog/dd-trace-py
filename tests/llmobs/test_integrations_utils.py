from ddtrace.llmobs._integrations.utils import _extract_chat_template_from_instructions
from ddtrace.llmobs._integrations.utils import _normalize_prompt_variables


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
