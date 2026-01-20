import re
from typing import Any
from typing import Dict
from typing import List
from typing import Literal
from typing import Optional
from typing import Union

from ddtrace.llmobs.types import Message
from ddtrace.llmobs.types import PromptLike


class ManagedPrompt:
    """
    An immutable prompt template retrieved from the Datadog Prompt Registry.

    STABLE API:
        - format(**vars) -> str | List[Dict]

    INTERNAL (may change):
        - All properties (id, version, label, source, template, template_type, variables)
    """

    # Private attributes (type hints for mypy)
    _id: str
    _version: str
    _label: str
    _source: Literal["registry", "cache", "fallback"]
    _template: Union[str, List[Dict[str, str]], List[Message]]
    _variables: List[str]

    def __init__(
        self,
        prompt_id: str,
        version: str,
        label: str,
        source: Literal["registry", "cache", "fallback"],
        template: Union[str, List[Dict[str, str]], List[Message]],
        variables: Optional[List[str]] = None,
    ) -> None:
        # Use object.__setattr__ to bypass our immutability guard
        object.__setattr__(self, "_id", prompt_id)
        object.__setattr__(self, "_version", version)
        object.__setattr__(self, "_label", label)
        object.__setattr__(self, "_source", source)
        object.__setattr__(self, "_template", template)
        object.__setattr__(self, "_variables", variables or [])

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("ManagedPrompt objects are immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("ManagedPrompt objects are immutable")

    @property
    def id(self) -> str:
        return self._id

    @property
    def version(self) -> str:
        return self._version

    @property
    def label(self) -> str:
        return self._label

    @property
    def source(self) -> Literal["registry", "cache", "fallback"]:
        return self._source

    @property
    def template(self) -> Union[str, List[Dict[str, str]], List[Message]]:
        return self._template

    @property
    def template_type(self) -> Literal["text", "chat"]:
        """Computed from template - 'text' for string, 'chat' for list."""
        return "text" if isinstance(self._template, str) else "chat"

    @property
    def variables(self) -> List[str]:
        return self._variables

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
        if isinstance(self._template, str):
            return self._render_text(self._template, variables)
        else:
            return self._render_chat(self._template, variables)

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
            "id": self._id,
            "version": self._version,
            "variables": variables if variables else {},
            "tags": {
                "dd.prompt.source": self._source,
                "dd.prompt.label": self._label,
            },
        }

        if isinstance(self._template, str):
            result["template"] = self._template
        else:
            result["chat_template"] = self._template

        return result

    def _render_text(self, template: str, variables: Dict[str, str]) -> str:
        """Render a text template with safe substitution."""
        return _safe_substitute(template, variables)

    def _render_chat(
        self, messages: Union[List[Dict[str, str]], List[Message]], variables: Dict[str, str]
    ) -> List[Dict[str, str]]:
        """Render each message's content with safe substitution."""
        rendered: List[Dict[str, str]] = []
        for msg in messages:
            rendered_msg: Dict[str, str] = {"role": str(msg.get("role", "")), "content": str(msg.get("content", ""))}
            rendered_msg["content"] = _safe_substitute(rendered_msg["content"], variables)
            rendered.append(rendered_msg)
        return rendered

    def __repr__(self) -> str:
        return (
            f"ManagedPrompt(id={self._id!r}, version={self._version!r}, label={self._label!r}, source={self._source!r})"
        )

    def _with_source(self, source: Literal["registry", "cache", "fallback"]) -> "ManagedPrompt":
        """Create a copy with a different source. Used internally for caching."""
        if self._source == source:
            return self
        return ManagedPrompt(
            prompt_id=self._id,
            version=self._version,
            label=self._label,
            source=source,
            template=self._template,
            variables=list(self._variables),
        )

    @classmethod
    def from_fallback(
        cls,
        prompt_id: str,
        label: str,
        fallback: PromptLike = None,
    ) -> "ManagedPrompt":
        """Create a ManagedPrompt from a fallback value.

        Args:
            prompt_id: The prompt identifier.
            label: The prompt label.
            fallback: A string, message list, Prompt dict, or callable returning any of those.

        Returns:
            A ManagedPrompt with source="fallback".
        """
        if fallback is None:
            return cls(
                prompt_id=prompt_id,
                version="fallback",
                label=label,
                source="fallback",
                template="",
                variables=[],
            )

        value = fallback() if callable(fallback) else fallback

        if isinstance(value, dict):
            template: Union[str, List[Dict[str, str]], List[Message]] = (
                value.get("template") or value.get("chat_template") or ""
            )
            version = value.get("version", "fallback") or "fallback"
        else:
            template = value
            version = "fallback"

        return cls(
            prompt_id=prompt_id,
            version=version,
            label=label,
            source="fallback",
            template=template,
            variables=[],
        )


_VARIABLE_PATTERN = re.compile(r"\{\s*(\w+)\s*\}")


def _safe_substitute(template: str, variables: Dict[str, str]) -> str:
    """
    Substitute {variable} placeholders with values from variables dict.

    Missing variables are left as-is (safe substitution).
    """

    def replace(match: re.Match) -> str:
        var_name = match.group(1)
        return str(variables.get(var_name, match.group(0)))

    return _VARIABLE_PATTERN.sub(replace, template)
