from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._prompts.prompt import ManagedPrompt
from ddtrace.llmobs.types import ChatMessage
from ddtrace.llmobs.types import ChatTemplateItem
from ddtrace.llmobs.types import Prompt
from ddtrace.llmobs.types import PromptVersionResponse


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


def type_checked_existing_outputs(prompt: ManagedPrompt, version: PromptVersionResponse, annotation: Prompt) -> None:
    formatted = prompt.format(name="Alice")
    if isinstance(formatted, list):
        formatted[0]["content"].upper()
    template = version["template"]
    if isinstance(template, list):
        template[0]["content"].upper()
    annotation["chat_template"][0]["content"].upper()
