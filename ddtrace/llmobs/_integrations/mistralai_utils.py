from typing import Any

from ddtrace.llmobs._constants import UNKNOWN_MODEL_PROVIDER


def extract_provider(kwargs: dict[str, Any]) -> str:
    server_url = kwargs.get("server_url") or ""
    return "mistral" if not server_url or "mistral" in server_url.lower() else UNKNOWN_MODEL_PROVIDER
