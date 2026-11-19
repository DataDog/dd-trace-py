"""Local dependency maps, published atomically inside Git's metadata directory."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import typing as t

from ddtrace.testing.internal.tia.artifacts import is_git_sha
from ddtrace.testing.internal.tia.selection import load_graph
from ddtrace.testing.internal.utils import _write_json_atomic


log = logging.getLogger(__name__)


class LocalMapStore:
    def __init__(self, directory: t.Optional[Path]) -> None:
        self.directory = directory

    def load(self, configuration_hash: str, commits: list[str]) -> t.Optional[dict[str, t.Any]]:
        if self.directory is None:
            return None
        for commit in commits:
            path = self.directory / configuration_hash / f"{commit}.json"
            try:
                document = json.loads(path.read_bytes())
            except (OSError, ValueError):
                continue
            if not isinstance(document, dict) or document.get("configuration_hash") != configuration_hash:
                continue
            graph = load_graph(document.get("map"), document)
            if (
                graph is not None
                and is_git_sha(graph.base_commit)
                and document.get("matched_commit", graph.base_commit) == commit
            ):
                return document
        return None

    def save(self, configuration_hash: str, document: dict[str, t.Any]) -> None:
        if self.directory is None:
            return
        matched_commit = document.get("matched_commit", document["base_commit"])
        path = self.directory / configuration_hash / f"{matched_commit}.json"
        try:
            _write_json_atomic(path, {**document, "configuration_hash": configuration_hash})
        except (OSError, TypeError, ValueError):
            log.debug("Could not cache TIA map locally", exc_info=True)
