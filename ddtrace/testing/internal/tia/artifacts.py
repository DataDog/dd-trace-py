"""Metadata and opaque bytes exchanged with the TIA artifact service."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import typing as t
from urllib.parse import urlencode


ARTIFACTS_ENDPOINT = "/api/unstable/ci/tia/artifacts"
MAX_ARTIFACT_BYTES = 5 * 1024 * 1024
MAX_LOOKUP_COMMITS = 100
_GIT_SHA = re.compile(r"(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})\Z")


def is_git_sha(value: t.Any) -> bool:
    return isinstance(value, str) and _GIT_SHA.fullmatch(value) is not None


@dataclass(frozen=True)
class ArtifactMetadata:
    repository_id: t.Union[str, int]
    suite_id: t.Union[str, int]
    env_hash: str
    language: str = "python"
    compatibility_version: int = 0
    configurations: t.Optional[dict[str, str]] = None

    def attributes(self) -> dict[str, t.Any]:
        result: dict[str, t.Any] = {
            "repository_id": self.repository_id,
            "suite_id": self.suite_id,
            "env_hash": self.env_hash,
            "language": self.language,
            "compatibility_version": self.compatibility_version,
        }
        if self.configurations is not None:
            result["configurations"] = self.configurations
        return result

    def upload_path(self, commit: str) -> str:
        query = self.attributes()
        query["git_commit_sha"] = commit
        if self.configurations is not None:
            query["configurations"] = json.dumps(self.configurations, sort_keys=True, separators=(",", ":"))
        return f"{ARTIFACTS_ENDPOINT}?{urlencode(query)}"


@dataclass(frozen=True)
class Artifact:
    content: bytes
    matched_commit: str
    source_commit: str
