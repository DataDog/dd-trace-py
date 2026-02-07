from dataclasses import dataclass
from dataclasses import replace
from typing import Any
from typing import Dict
from typing import List
from typing import Literal
from typing import Optional
from typing import Union

from ddtrace.llmobs._prompts.utils import extract_template
from ddtrace.llmobs._prompts.utils import render_chat
from ddtrace.llmobs._prompts.utils import safe_substitute
from ddtrace.llmobs.types import Message
from ddtrace.llmobs.types import PromptFallback


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
    label: Optional[str]
    source: Literal["registry", "cache", "fallback"]
    template: Union[str, List[Message]]
    _uuid: Optional[str] = None
    _version_uuid: Optional[str] = None

    def format(self, **variables: str) -> Union[str, List[Message]]:
        """
        Render the template with variables.

        For text templates, returns the rendered string.
        For chat templates, returns the rendered message list.

        Uses safe substitution: missing variables are left as placeholders.

        Args:
            **variables: Template variables to substitute

        Returns:
            str (for text templates) or List[Message] (for chat templates)
        """
        if isinstance(self.template, str):
            return safe_substitute(self.template, variables)
        return render_chat(self.template, variables)

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
        }
        if variables:
            result["variables"] = variables
        if self.label:
            result["label"] = self.label
        if self._uuid:
            result["prompt_uuid"] = self._uuid
        if self._version_uuid:
            result["prompt_version_uuid"] = self._version_uuid

        if isinstance(self.template, str):
            result["template"] = self.template
        else:
            result["chat_template"] = self.template

        return result

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
            "_uuid": self._uuid,
            "_version_uuid": self._version_uuid,
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
            _uuid=data.get("_uuid"),
            _version_uuid=data.get("_version_uuid"),
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
        fallback: PromptFallback = None,
    ) -> "ManagedPrompt":
        """Create a ManagedPrompt from a fallback value.

        Args:
            prompt_id: The prompt identifier.
            fallback: A string, message list, Prompt dict, or callable returning any of those.

        Returns:
            A ManagedPrompt with source="fallback" and label=None.
        """
        template: Union[str, List[Message]] = ""
        version = "fallback"

        if fallback is not None:
            value = fallback() if callable(fallback) else fallback
            if isinstance(value, dict):
                template = extract_template(value)
                version = value.get("version") or "fallback"
            else:
                template = value

        return cls(
            id=prompt_id,
            version=version,
            label=None,
            source="fallback",
            template=template,
        )
