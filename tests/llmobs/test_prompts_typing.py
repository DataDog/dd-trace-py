from ddtrace.llmobs import LLMObs
from ddtrace.llmobs.types import ChatMessage
from ddtrace.llmobs.types import ChatTemplateItem


def test_create_prompt_authoring_types_are_source_compatible():
    def type_checked_calls() -> None:
        existing_messages: list[ChatMessage] = [{"role": "user", "content": "Hello"}]
        LLMObs.create_prompt("existing", existing_messages)
        LLMObs.create_prompt_version("existing", existing_messages)

        placeholder_template: list[ChatTemplateItem] = [
            {"role": "system", "content": "Be concise"},
            {"type": "placeholder", "name": "history"},
        ]
        LLMObs.create_prompt("placeholder", placeholder_template)
        LLMObs.create_prompt_version("placeholder", placeholder_template)

    assert callable(type_checked_calls)
