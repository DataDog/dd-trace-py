# Adapted from pytest-filemon (Apache-2.0).
"""File-level test selection: recorded per-test coverage dependencies vs. changed files.

A dependency is a repository file.  A test runs when it has no recorded
data, failed in the recorded run, one of its dependency files changed,
any shared import-time dependency changed, or it spawned a process
(`always`: re-run whenever anything changed).
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from pathlib import PurePosixPath
import typing as t


DATA_VERSION = 1


@dataclass
class Graph:
    """The recorded dependency data from the last run."""

    base_commit: str | None
    tests: dict[str, set[str]]
    import_deps: set[str]
    failed: set[str]
    # Tests that spawned a process: no dependencies, re-run every time.
    always: set[str]


def load_graph(data: t.Any, meta: t.Any) -> Graph | None:
    """Parse the stored data, or None if it is unusable (fresh/corrupt)."""
    if not isinstance(data, dict) or not isinstance(meta, dict):
        return None
    try:
        version = data.get("version")
        if type(version) is not int or version != DATA_VERSION:
            return None
        files = data["files"]
        if not isinstance(files, list) or any(
            not isinstance(path, str)
            or not path
            or PurePosixPath(path).is_absolute()
            or ".." in PurePosixPath(path).parts
            or "\\" in path
            for path in files
        ):
            return None
        raw_tests = data["tests"]
        if not isinstance(raw_tests, dict):
            return None
        tests = {}
        for nid, indices in raw_tests.items():
            if not isinstance(nid, str) or not isinstance(indices, list):
                return None
            own = set()
            for i in indices:
                if type(i) is not int or not (0 <= i < len(files)):
                    return None
                own.add(files[i])
            tests[nid] = own
        import_deps = set()
        if not isinstance(data.get("import_deps"), list):
            return None
        for i in data["import_deps"]:
            if type(i) is not int or not (0 <= i < len(files)):
                return None
            import_deps.add(files[i])
        raw_failed = data.get("failed", [])
        if not isinstance(raw_failed, list) or any(not isinstance(nid, str) for nid in raw_failed):
            return None
        failed = {f for f in raw_failed if isinstance(f, str)}
        raw_always = data.get("always", [])
        if not isinstance(raw_always, list) or any(not isinstance(nid, str) for nid in raw_always):
            return None
        always = {a for a in raw_always if isinstance(a, str)} if isinstance(raw_always, list) else set()
        base_commit = meta.get("base_commit")
        if base_commit is not None and not isinstance(base_commit, str):
            return None
    except Exception:
        return None
    return Graph(
        base_commit=base_commit,
        tests=tests,
        import_deps=import_deps,
        failed=failed,
        always=always,
    )


@dataclass
class Selection:
    """Outcome of the selection for one run."""

    selected: list[str]
    deselected: list[str]
    changed: set[str] = field(default_factory=set)
    reason: str = ""


def select_tests(
    collected: list[str],
    graph: Graph | None,
    changed: set[str] | None,
) -> Selection:
    """Decide which of *collected* tests should run."""
    if graph is None:
        return Selection(list(collected), [], set(), "no stored data; running everything")
    if changed is None:
        return Selection(
            list(collected),
            [],
            set(),
            "cannot compute changes since the recorded base commit; running everything",
        )
    import_stale = not graph.import_deps.isdisjoint(changed)
    selected: list[str] = []
    deselected: list[str] = []
    n_affected = n_failed = n_new = n_always = 0
    for nodeid in collected:
        deps = graph.tests.get(nodeid)
        if nodeid in graph.always:
            selected.append(nodeid)
            n_always += 1
        elif deps is None:
            selected.append(nodeid)
            n_new += 1
        elif nodeid in graph.failed:
            selected.append(nodeid)
            n_failed += 1
        elif import_stale or not deps.isdisjoint(changed):
            selected.append(nodeid)
            n_affected += 1
        else:
            deselected.append(nodeid)
    base = (graph.base_commit or "?")[:8]
    reason = (
        f"{len(changed)} file(s) changed since {base}: {n_affected} affected, {n_failed} previously failed, {n_new} new"
    )
    if import_stale:
        reason += "; import-time dependencies changed"
    if n_always:
        reason += f"; {n_always} always re-run (spawn subprocesses)"
    return Selection(selected, deselected, set(changed), reason)
