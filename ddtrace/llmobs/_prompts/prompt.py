import re
from typing import Any
from typing import Dict
from typing import List
from typing import Literal
from typing import Optional
from typing import Tuple
from typing import Union


class ManagedPrompt:
    """
    An immutable prompt template retrieved from the Datadog Prompt Registry.

    STABLE API:
        - format(**vars) -> str | List[Dict]

    INTERNAL (may change):
        - All properties (id, version, label, source, template, template_type, variables)
        - Convenience methods (to_messages, to_anthropic)
    """

    # Private attributes (type hints for mypy)
    _id: str
    _version: str
    _label: str
    _source: Literal["registry", "cache", "fallback"]
    _template: Union[str, List[Dict[str, str]]]
    _variables: List[str]

    def __init__(
        self,
        prompt_id: str,
        version: str,
        label: str,
        source: Literal["registry", "cache", "fallback"],
        template: Union[str, List[Dict[str, str]]],
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
    def template(self) -> Union[str, List[Dict[str, str]]]:
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

    def to_messages(self, **variables: str) -> List[Dict[str, str]]:
        """
        Render and convert to OpenAI-compatible message format.

        For text templates, wraps in a single user message.
        For chat templates, returns the rendered message list.
        """
        if isinstance(self._template, str):
            rendered = self._render_text(self._template, variables)
            return [{"role": "user", "content": rendered}]
        else:
            return self._render_chat(self._template, variables)

    def to_anthropic(self, **variables: str) -> Tuple[Optional[str], List[Dict[str, str]]]:
        """
        Render and convert to Anthropic format (system, messages).

        Returns:
            Tuple of (system_message, messages)
        """
        messages = self.to_messages(**variables)
        system_messages: List[str] = []
        filtered_messages: List[Dict[str, str]] = []

        for msg in messages:
            if msg.get("role") == "system":
                content = msg.get("content", "")
                if content:
                    system_messages.append(content)
            else:
                filtered_messages.append(msg)

        # Concatenate multiple system messages with newlines
        system_message = "\n".join(system_messages) if system_messages else None
        return system_message, filtered_messages

    def to_annotation_dict(self) -> Dict[str, Any]:
        """
        Convert to the format expected by annotation_context.

        Note: Variables are NOT included here. They should be passed separately
        via the prompt_variables= parameter in annotation_context().

        This matches the existing Prompt TypedDict in ddtrace/llmobs/types.py
        """
        result: Dict[str, Any] = {
            "id": self._id,
            "version": self._version,
            "variables": {},  # Variables come from prompt_variables= param
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

    def _render_chat(self, messages: List[Dict[str, str]], variables: Dict[str, str]) -> List[Dict[str, str]]:
        """Render each message's content with safe substitution."""
        rendered = []
        for msg in messages:
            rendered_msg = dict(msg)
            if "content" in rendered_msg:
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


_VARIABLE_PATTERN = re.compile(r"\{\{(\w+)\}\}")


def _safe_substitute(template: str, variables: Dict[str, str]) -> str:
    """
    Substitute {{variable}} placeholders with values from variables dict.

    Missing variables are left as-is (safe substitution).
    """

    def replace(match: re.Match) -> str:
        var_name = match.group(1)
        return str(variables.get(var_name, match.group(0)))

    return _VARIABLE_PATTERN.sub(replace, template)
