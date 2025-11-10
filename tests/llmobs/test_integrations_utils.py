from ddtrace.llmobs._integrations.utils import _extract_chat_template_from_instructions


class TestExtractChatTemplateFromInstructions:
    """Tests for the _extract_chat_template_from_instructions function."""

    def test_basic_functionality(self):
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

    def test_overlapping_values_and_partial_matches(self):
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

    def test_special_characters_and_escaping(self):
        """Test that regex special characters are properly escaped."""
        instructions = [
            {
                "role": "user",
                "content": [{"text": "The price is $99.99 (plus $5.00 tax)"}],
            }
        ]
        variables = {"price": "$99.99", "tax": "$5.00"}

        result = _extract_chat_template_from_instructions(instructions, variables)

        assert result[0]["content"] == "The price is {{price}} (plus {{tax}} tax)"

    def test_empty_and_edge_cases(self):
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

    def test_response_input_text_objects(self):
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

        result = _extract_chat_template_from_instructions(instructions, variables)

        # Also tests that multiple content items are concatenated
        assert result[0]["content"] == "Part one Question: {{question}}"
