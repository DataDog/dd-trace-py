import json
import re
from typing import Any
from typing import Mapping
from typing import Optional
from typing import Union

from ddtrace.llmobs.types import Message


_VARIABLE_PATTERN = re.compile(r"\{\{?\s*(\w+)\s*\}\}?")


def extract_template(data: Mapping[str, Any], default: Union[str, list[Message]] = "") -> Union[str, list[Message]]:
    """Extract template from a dict, checking both 'template' and 'chat_template' keys."""
    return data.get("template") or data.get("chat_template") or default


def safe_substitute(template: str, variables: dict[str, str]) -> str:
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


def render_chat(messages: list[Message], variables: dict[str, str]) -> list[Message]:
    """Render each message's content with safe substitution."""
    rendered: list[Message] = []
    for msg in messages:
        role = msg.get("role") or ""
        content = msg.get("content") or ""
        rendered.append({"role": role, "content": safe_substitute(content, variables)})
    return rendered
