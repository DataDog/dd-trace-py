import json
import re
from typing import TYPE_CHECKING
from typing import Any
from typing import Mapping
from typing import Optional
from typing import Union

from ddtrace.llmobs.types import ChatTemplateItem
from ddtrace.llmobs.types import Message


if TYPE_CHECKING:
    from typing_extensions import TypeGuard


_VARIABLE_PATTERN = re.compile(r"\{\{?\s*(\w+)\s*\}\}?")


def extract_template(
    data: Mapping[str, Any], default: Union[str, list[ChatTemplateItem]] = ""
) -> Union[str, list[ChatTemplateItem]]:
    """Extract template from a dict, checking both 'template' and 'chat_template' keys."""
    return data.get("template") or data.get("chat_template") or default


def safe_substitute(template: str, variables: Mapping[str, Any]) -> str:
    """
    Substitute {variable} or {{variable}} placeholders with values from variables dict.

    Missing variables are left as-is (safe substitution).
    """

    def replace_var(match: re.Match) -> str:
        var_name = match.group(1)
        return str(variables.get(var_name, match.group(0)))

    return _VARIABLE_PATTERN.sub(replace_var, template)


def extract_error_detail(body: str) -> str:
    try:
        return json.loads(body).get("detail", body)
    except Exception:
        return body


def cache_key(prompt_id: str, label: Optional[str]) -> str:
    return f"{prompt_id}:{label or ''}"


def _is_message(value: object) -> "TypeGuard[Message]":
    if not isinstance(value, dict) or not isinstance(value.get("role"), str):
        return False
    if value.get("type") == "placeholder":
        return False
    content = value.get("content")
    if content is not None:
        return isinstance(content, str)
    for field in ("tool_calls", "tool_results"):
        items = value.get(field)
        if isinstance(items, list) and items:
            return True
    return False


def render_chat(messages: list[ChatTemplateItem], variables: dict[str, Any]) -> list[Message]:
    """Render authored messages and expand named runtime message lists."""
    rendered: list[Message] = []
    for msg in messages:
        if msg.get("type") == "placeholder":
            name = msg.get("name")
            if not isinstance(name, str) or not name:
                raise ValueError("Message placeholder must have a non-empty string name")
            if name not in variables:
                raise ValueError(f"Missing value for message placeholder '{name}'")
            value = variables[name]
            if not isinstance(value, list):
                raise ValueError(f"Message placeholder '{name}' must be a list of messages")
            for message in value:
                if not _is_message(message):
                    raise ValueError(
                        f"Message placeholder '{name}' must contain messages with "
                        "a string role and text or tool content"
                    )
                rendered.append(message.copy())
            continue
        role = msg.get("role")
        content = msg.get("content")
        if not isinstance(role, str):
            role = ""
        if not isinstance(content, str):
            content = ""
        rendered.append({"role": role, "content": safe_substitute(content, variables)})
    return rendered
