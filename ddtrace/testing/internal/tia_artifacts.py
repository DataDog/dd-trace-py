"""TIA artifact upload/lookup via the ITRv2 function-level TIA endpoints.

When enabled (``_DD_CIVISIBILITY_ITR_V2_ENABLED=1``), the pytest plugin uses
these endpoints instead of CI artifacts to persist and retrieve the pytest-testmon
dependency database:

* **Lookup** (before tests): ``POST /api/unstable/ci/tia/artifacts/lookup``
  walks the commit ancestry and streams the nearest compatible database back.
* **Upload** (after tests): ``PUT /api/unstable/ci/tia/artifacts`` stores the
  gzipped database bytes with metadata in query params.

The library never sees ``org_id`` — it is resolved server-side from the API key.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import typing as t
from urllib.parse import urlencode

from ddtrace.testing.internal.git import Git
from ddtrace.testing.internal.git import GitTag
from ddtrace.testing.internal.http import BackendConnector
from ddtrace.testing.internal.http import BackendConnectorSetup
from ddtrace.testing.internal.http import Subdomain


log = logging.getLogger(__name__)

__all__ = [
    "TIAArtifactClient",
    "TIAArtifactResult",
    "compute_env_hash",
    "compute_suite_id",
    "is_tia_v2_enabled",
]

_API_BASE = "/api/unstable/ci/tia"
_UPLOAD_PATH = f"{_API_BASE}/artifacts"
_LOOKUP_PATH = f"{_API_BASE}/artifacts/lookup"

_MAX_ANCESTORS = 100
_COMPATIBILITY_VERSION = 0

_ENV_VAR = "_DD_CIVISIBILITY_ITR_V2_ENABLED"


def is_tia_v2_enabled() -> bool:
    """Return whether the ITRv2 TIA artifact endpoints should be used."""
    return os.environ.get(_ENV_VAR, "").lower() == "true"


def compute_env_hash() -> str:
    """Compute a hash of the Python major/minor version + installed packages.

    Uses ``importlib.metadata`` (stdlib) so it works regardless of the package
    manager (pip, uv, poetry, etc.).  This identifies the test environment so
    that databases are only reused across commits with the same Python version
    and installed packages.
    """
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    hasher = hashlib.sha256()
    hasher.update(python_version.encode("utf-8"))
    hasher.update(b"\n")

    try:
        import importlib.metadata as importlib_metadata

        # distributions() returns all installed distributions; sort for determinism.
        dists = sorted(importlib_metadata.distributions(), key=lambda d: d.metadata["Name"] or "")
        for dist in dists:
            name = dist.metadata["Name"] or ""
            version = dist.version or ""
            hasher.update(f"{name}=={version}".encode())
            hasher.update(b"\n")
    except Exception:
        log.warning("Failed to enumerate installed packages; env_hash will only include Python version", exc_info=True)

    return hasher.hexdigest()


def compute_suite_id(config: t.Any) -> str:
    """Compute a suite_id from the pytest invocation arguments.

    Takes the last positional argument (pytest convention puts the test path last).
    Falls back to "pytest" if no path is found.
    """
    invocation_params = getattr(config, "invocation_params", None)
    if invocation_params is None:
        return "pytest"

    args = list(invocation_params.args)
    # Find the last path-like argument (not a flag or flag value).
    _FLAGS_WITH_VALUE = {"-p", "--plugin", "-k", "--ignore", "-m", "--markers"}
    skip_next = False
    path_arg = None
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg.startswith("-"):
            if arg in _FLAGS_WITH_VALUE:
                skip_next = True
            continue
        path_arg = arg

    if path_arg is None:
        return "pytest"

    return str(path_arg.replace("/", ".").replace("\\", ".").rstrip("."))


class TIAArtifactResult(t.NamedTuple):
    """Result of a TIA artifact lookup or upload."""

    success: bool
    database_bytes: t.Optional[bytes]
    source_commit: t.Optional[str]
    matched_commit: t.Optional[str]
    deduplicated: t.Optional[bool]
    error: t.Optional[str]


class TIAArtifactClient:
    """Client for the ITRv2 TIA artifact upload/lookup endpoints."""

    def __init__(
        self,
        connector_setup: BackendConnectorSetup,
        *,
        env_tags: t.Optional[dict[str, str]] = None,
        workspace_path: t.Optional[Path] = None,
    ) -> None:
        self._connector: BackendConnector = connector_setup.get_connector_for_subdomain(Subdomain.API)
        self._env_tags = env_tags
        self._workspace_path = workspace_path
        self._env_hash = compute_env_hash()
        self._repository_id = self._resolve_repository_id(env_tags, workspace_path)
        self._commit_sha = self._resolve_commit_sha(env_tags, workspace_path)
        self._ancestry: t.Optional[list[str]] = None

    @staticmethod
    def _resolve_repository_id(env_tags: t.Optional[dict[str, str]], workspace_path: t.Optional[Path]) -> str:
        """Return the repository identifier from env tags or the Git client."""
        if env_tags:
            value = env_tags.get(GitTag.REPOSITORY_URL)
            if value:
                return value
        try:
            git = Git(cwd=str(workspace_path) if workspace_path else None)
            return git.get_repository_url()
        except Exception:
            log.debug("Could not resolve repository URL", exc_info=True)
            return "unknown"

    @staticmethod
    def _resolve_commit_sha(env_tags: t.Optional[dict[str, str]], workspace_path: t.Optional[Path]) -> str:
        """Return the current commit SHA from env tags or the Git client."""
        if env_tags:
            value = env_tags.get(GitTag.COMMIT_SHA)
            if value:
                return value
        try:
            git = Git(cwd=str(workspace_path) if workspace_path else None)
            return git.get_commit_sha()
        except Exception:
            log.debug("Could not resolve commit SHA", exc_info=True)
            return ""

    def _get_git_ancestry(self) -> list[str]:
        """Return the commit ancestry (nearest-first), capped at ``_MAX_ANCESTORS``.

        Results are cached on the client instance so repeated calls do not
        spawn additional git subprocesses.
        """
        if self._ancestry is not None:
            return self._ancestry
        try:
            git = Git(cwd=str(self._workspace_path) if self._workspace_path else None)
            output = git._git_output(
                ["rev-list", "--max-count", str(_MAX_ANCESTORS), "HEAD"],
            )
            self._ancestry = [line.strip() for line in output.strip().splitlines() if line.strip()]
        except Exception:
            log.warning("Failed to get git ancestry", exc_info=True)
            self._ancestry = []
        return self._ancestry

    def lookup(
        self,
        suite_id: str,
        *,
        configurations: t.Optional[dict[str, str]] = None,
    ) -> TIAArtifactResult:
        """Look up the nearest compatible ancestor database.

        Returns a :class:`TIAArtifactResult` with ``database_bytes`` set to the
        raw (decompressed) database bytes on success, or ``success=False`` on
        failure (404, 503, network error, etc.).
        """
        ancestry = self._get_git_ancestry()
        if not ancestry:
            return TIAArtifactResult(
                success=False,
                database_bytes=None,
                source_commit=None,
                matched_commit=None,
                deduplicated=None,
                error="no git ancestry available",
            )

        body = {
            "data": {
                "type": "ci_app_tia_artifact_lookup",
                "id": "req-1",
                "attributes": {
                    "repository_id": self._repository_id,
                    "suite_id": suite_id,
                    "env_hash": self._env_hash,
                    "configurations": configurations or {},
                    "compatibility_version": _COMPATIBILITY_VERSION,
                    "git_commit_shas": ancestry,
                },
            }
        }

        result = self._connector.request(
            "POST",
            path=_LOOKUP_PATH,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/vnd.api+json"},
            is_json_response=False,
        )

        if result.error_type:
            error_desc = result.error_description or "unknown error"
            log.debug("TIA artifact lookup failed: %s", error_desc)
            return TIAArtifactResult(
                success=False,
                database_bytes=None,
                source_commit=None,
                matched_commit=None,
                deduplicated=None,
                error=error_desc,
            )

        status = result.response.status if result.response else 0
        if status == 404:
            log.debug("TIA artifact lookup: no compatible artifact found")
            return TIAArtifactResult(
                success=False,
                database_bytes=None,
                source_commit=None,
                matched_commit=None,
                deduplicated=None,
                error="no compatible artifact found",
            )
        if status == 503:
            log.debug("TIA artifact lookup: backend not ready")
            return TIAArtifactResult(
                success=False,
                database_bytes=None,
                source_commit=None,
                matched_commit=None,
                deduplicated=None,
                error="backend not ready",
            )
        if status != 200:
            return TIAArtifactResult(
                success=False,
                database_bytes=None,
                source_commit=None,
                matched_commit=None,
                deduplicated=None,
                error=f"unexpected status {status}",
            )

        # Response body is the raw gzipped database bytes.
        raw_bytes = result.response_body or b""
        try:
            database_bytes = gzip.decompress(raw_bytes)
        except Exception:
            # Maybe the server didn't gzip it — try raw.
            database_bytes = raw_bytes

        matched_commit = None
        source_commit = None
        if result.response:
            matched_commit = result.response.headers.get("x-dd-tia-git-commit-sha")
            source_commit = result.response.headers.get("x-dd-tia-source-commit")

        log.debug(
            "TIA artifact lookup succeeded: %d bytes (matched=%s, source=%s)",
            len(database_bytes),
            matched_commit,
            source_commit,
        )

        return TIAArtifactResult(
            success=True,
            database_bytes=database_bytes,
            source_commit=source_commit,
            matched_commit=matched_commit,
            deduplicated=None,
            error=None,
        )

    def upload(
        self,
        database_path: Path,
        suite_id: str,
        *,
        configurations: t.Optional[dict[str, str]] = None,
    ) -> TIAArtifactResult:
        """Upload the testmon database for the current commit.

        Reads the database file, gzips it, and PUTs it to the artifact endpoint.
        Returns a :class:`TIAArtifactResult` with ``success=True`` on 201.
        """
        if not database_path.exists():
            log.debug("TIA artifact upload: database file does not exist at %s", database_path)
            return TIAArtifactResult(
                success=False,
                database_bytes=None,
                source_commit=None,
                matched_commit=None,
                deduplicated=None,
                error="database file does not exist",
            )

        # Read and gzip the database.
        raw_bytes = database_path.read_bytes()
        gzipped_bytes = gzip.compress(raw_bytes, compresslevel=6)

        params = {
            "repository_id": self._repository_id,
            "suite_id": suite_id,
            "env_hash": self._env_hash,
            "git_commit_sha": self._commit_sha,
            "compatibility_version": str(_COMPATIBILITY_VERSION),
        }
        if configurations:
            params["configurations"] = json.dumps(configurations, sort_keys=True)

        path = f"{_UPLOAD_PATH}?{urlencode(params)}"

        result = self._connector.request(
            "PUT",
            path=path,
            data=gzipped_bytes,
            headers={"Content-Type": "application/octet-stream"},
            is_json_response=True,
        )

        if result.error_type:
            error_desc = result.error_description or "unknown error"
            log.warning("TIA artifact upload failed: %s", error_desc)
            return TIAArtifactResult(
                success=False,
                database_bytes=None,
                source_commit=None,
                matched_commit=None,
                deduplicated=None,
                error=error_desc,
            )

        status = result.response.status if result.response else 0
        if status == 503:
            log.debug("TIA artifact upload: backend not ready")
            return TIAArtifactResult(
                success=False,
                database_bytes=None,
                source_commit=None,
                matched_commit=None,
                deduplicated=None,
                error="backend not ready",
            )
        if status != 201:
            return TIAArtifactResult(
                success=False,
                database_bytes=None,
                source_commit=None,
                matched_commit=None,
                deduplicated=None,
                error=f"unexpected status {status}",
            )

        deduplicated = None
        if result.parsed_response:
            attrs = result.parsed_response.get("data", {}).get("attributes", {})
            deduplicated = attrs.get("deduplicated")

        log.debug(
            "TIA artifact upload succeeded: %d bytes (gzipped %d, deduplicated=%s)",
            len(raw_bytes),
            len(gzipped_bytes),
            deduplicated,
        )

        return TIAArtifactResult(
            success=True,
            database_bytes=None,
            source_commit=None,
            matched_commit=None,
            deduplicated=deduplicated,
            error=None,
        )

    def close(self) -> None:
        """Close the underlying HTTP connector."""
        try:
            self._connector.close()
        except Exception:  # nosec: B110
            pass
