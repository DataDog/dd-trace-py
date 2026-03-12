from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._prompts.prompt import ManagedPrompt
from ddtrace.llmobs._prompts.utils import extract_template


log = get_logger(__name__)


def parse_ff_prompt_payload(value: dict) -> Optional[ManagedPrompt]:
    """Parse a Feature Flag variant payload into a ManagedPrompt.

    The payload shape matches the Go PromptToFlagPayload serialization.
    Returns None if the payload is missing required fields or has no
    template content.
    """
    try:
        prompt_id = value.get("prompt_id")
        version = value.get("version")
        if not prompt_id or not version:
            log.debug("FF prompt payload missing prompt_id or version: %s", value)
            return None

        template = extract_template(value)
        if not template:
            log.debug("FF prompt payload has no template content: prompt_id=%s", prompt_id)
            return None

        return ManagedPrompt(
            id=prompt_id,
            version=version,
            label=value.get("label"),
            source="registry",
            template=template,
            _uuid=value.get("prompt_uuid"),
            _version_uuid=value.get("prompt_version_uuid"),
        )
    except Exception:
        log.debug("Failed to parse FF prompt payload", exc_info=True)
        return None
