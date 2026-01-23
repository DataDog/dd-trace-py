from dataclasses import dataclass
from dataclasses import replace
import re
from typing import Any
from typing import Dict
from typing import List
from typing import Literal
from typing import Mapping
from typing import Union

from ddtrace.llmobs.types import PromptFallback
from ddtrace.llmobs.types import TemplateContent


def _extract_template(data: Mapping[str, Any], default: TemplateContent = "") -> TemplateContent:
    """Extract template from a dict, checking both 'template' and 'chat_template' keys."""
    return data.get("template") or data.get("chat_template") or default


@dataclass(frozen=True)
class ManagedPrompt:
    """
    An immutable prompt template retrieved from the Datadog Prompt Registry.

    STABLE API:
        - format(**vars) -> str | List[Dict]
        - to_annotation_dict(**vars) -> Dict[str, Any]

    INTERNAL (may change):
        - All fields (id, version, label, source, template)
    """

    id: str
    version: str
    label: str
    source: Literal["registry", "cache", "fallback"]
    template: TemplateContent

    def format(self, **variables: str) -> Union[str, List[Dict[str, str]]]:
        """
        Render the template with variables.

        For text templates, returns the rendered string.
        For chat templates, returns the rendered message list.

        Uses safe substitution: missing variables are left as placeholders.

        Args:
            **variables: Template variables to substitute

        Returns:
            str (for text templates) or List[Dict[str, str]] (for chat templates)
        """
        if isinstance(self.template, str):
            return _safe_substitute(self.template, variables)
        else:
            return self._render_chat(self.template, variables)

    def to_annotation_dict(self, **variables: Any) -> Dict[str, Any]:
        """
        Convert to the format expected by annotation_context.

        Args:
            **variables: Variable names and values used in the prompt template.
                         Pass the same kwargs here that you use with format().

        Example:
            prompt.to_annotation_dict(name="Alice", topic="weather")
        """
        result: Dict[str, Any] = {
            "id": self.id,
            "version": self.version,
            "variables": variables if variables else {},
            "tags": {
                "dd.prompt.source": self.source,
                "dd.prompt.label": self.label,
            },
        }

        if isinstance(self.template, str):
            result["template"] = self.template
        else:
            result["chat_template"] = self.template

        return result

    def _render_chat(self, messages: List[Dict[str, str]], variables: Dict[str, str]) -> List[Dict[str, str]]:
        """Render each message's content with safe substitution."""
        rendered: List[Dict[str, str]] = []
        for msg in messages:
            role = msg.get("role") or ""
            content = msg.get("content") or ""
            rendered.append({"role": role, "content": _safe_substitute(content, variables)})
        return rendered

    def __repr__(self) -> str:
        return f"ManagedPrompt(id={self.id!r}, version={self.version!r}, label={self.label!r}, source={self.source!r})"

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "id": self.id,
            "version": self.version,
            "label": self.label,
            "source": self.source,
            "template": self.template,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ManagedPrompt":
        """Deserialize from a dict."""
        return cls(
            id=data["id"],
            version=data["version"],
            label=data["label"],
            source=data.get("source", "cache"),
            template=data["template"],
        )

    def _with_source(self, source: Literal["registry", "cache", "fallback"]) -> "ManagedPrompt":
        """Create a copy with a different source. Used internally for caching."""
        if self.source == source:
            return self
        return replace(self, source=source)

    @classmethod
    def from_fallback(
        cls,
        prompt_id: str,
        label: str,
        fallback: PromptFallback = None,
    ) -> "ManagedPrompt":
        """Create a ManagedPrompt from a fallback value.

        Args:
            prompt_id: The prompt identifier.
            label: The prompt label.
            fallback: A string, message list, Prompt dict, or callable returning any of those.

        Returns:
            A ManagedPrompt with source="fallback".
        """
        template: TemplateContent = ""
        version = "fallback"

        if fallback is not None:
            value = fallback() if callable(fallback) else fallback
            if isinstance(value, dict):
                template = _extract_template(value, default="")
                version = value.get("version") or "fallback"
            else:
                template = value

        return cls(
            id=prompt_id,
            version=version,
            label=label,
            source="fallback",
            template=template,
        )


_VARIABLE_PATTERN = re.compile(r"\{\{?\s*(\w+)\s*\}\}?")


def _safe_substitute(template: str, variables: Dict[str, str]) -> str:
    """
    Substitute {variable} or {{variable}} placeholders with values from variables dict.

    Missing variables are left as-is (safe substitution).
    """

    def replace_var(match: re.Match) -> str:
        var_name = match.group(1)
        return str(variables.get(var_name, match.group(0)))

    return _VARIABLE_PATTERN.sub(replace_var, template)
