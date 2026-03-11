import pytest

from ddtrace.llmobs._prompts.ff_parser import parse_ff_prompt_payload


class TestParseFFPromptPayload:
    def test_valid_text_template(self):
        payload = {
            "prompt_id": "greeting",
            "prompt_uuid": "uuid-1",
            "prompt_version_uuid": "uuid-2",
            "version": "v1",
            "label": "production",
            "template": "Hello {{name}}!",
        }
        prompt = parse_ff_prompt_payload(payload)
        assert prompt is not None
        assert prompt.id == "greeting"
        assert prompt.version == "v1"
        assert prompt.label == "production"
        assert prompt.source == "registry"
        assert prompt.template == "Hello {{name}}!"
        assert prompt._uuid == "uuid-1"
        assert prompt._version_uuid == "uuid-2"

    def test_valid_chat_template(self):
        payload = {
            "prompt_id": "assistant",
            "prompt_uuid": "uuid-1",
            "prompt_version_uuid": "uuid-2",
            "version": "v2",
            "label": "production",
            "chat_template": [
                {"role": "system", "content": "You help."},
                {"role": "user", "content": "{{q}}"},
            ],
        }
        prompt = parse_ff_prompt_payload(payload)
        assert prompt is not None
        assert prompt.id == "assistant"
        assert isinstance(prompt.template, list)
        assert len(prompt.template) == 2
        assert prompt.template[0]["role"] == "system"
        assert prompt.template[1]["content"] == "{{q}}"

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({"version": "1", "template": "hi"}, id="missing_prompt_id"),
            pytest.param({"prompt_id": "x", "template": "hi"}, id="missing_version"),
            pytest.param({"prompt_id": "x", "version": "1"}, id="missing_template"),
        ],
    )
    def test_missing_required_fields(self, payload):
        assert parse_ff_prompt_payload(payload) is None
