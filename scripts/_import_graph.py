"""Shared import-graph helpers used by ddtrace_module_graph.py and modularization_metrics.py.

Not a uv run-script — plain importable module. Both scripts add ``scripts/`` to sys.path
when run, so ``from _import_graph import ...`` works from either.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path
from typing import Optional


def path_to_module(root: Path, py_file: Path) -> tuple[str, bool]:
    """Return (fully qualified module name, True if this file is a package __init__)."""
    rel = py_file.relative_to(root).with_suffix("")
    parts = rel.parts
    is_pkg_init = parts[-1] == "__init__"
    if is_pkg_init:
        parts = parts[:-1]
    return ".".join(("ddtrace",) + parts), is_pkg_init


def path_bucket(py_file: Path, ddtrace_root: Path, detailed_components: tuple[str, ...]) -> Optional[str]:
    """Subsystem for the file path (first directory under ``ddtrace/``, else the root package)."""
    rel = py_file.relative_to(ddtrace_root)
    parts = rel.parts
    if len(parts) == 1:
        return None
    top = parts[0]
    if not top or top in detailed_components or top.startswith("."):
        return None
    for component in detailed_components:
        component_parts = component.split(".")
        comparison = list(zip(component_parts, parts))
        if all(c == p for c, p in comparison):
            return f"ddtrace.{component}.{parts[len(component_parts)]}"
    return f"ddtrace.{top}"


def import_target_bucket(
    mod: str, detailed_components: tuple[str, ...], extra_components: tuple[str, ...]
) -> Optional[str]:
    """Bucket import targets the same way as ``path_bucket`` (root modules collapse to None)."""
    choices = []
    if mod == "ddtrace":
        return None
    rest = mod[len("ddtrace.") :]
    if not rest:
        return None
    first, _, _ = rest.partition(".")
    parts = rest.split(".")
    if not first or first.startswith("."):
        return None
    for component in detailed_components:
        component_parts = component.split(".")
        comparison = list(zip(component_parts, parts))
        if all(c == p for c, p in comparison):
            choices.append("ddtrace." + ".".join(parts[: len(component_parts) + 1]))
    for component in extra_components:
        if mod.startswith(component):
            choices.append(component)
    if not mod.startswith("ddtrace."):
        choices.append(mod)
    choices.append(f"ddtrace.{first}")
    winner = sorted(choices, key=lambda a: len(a.split(".")))[-1]
    return winner


def resolve_relative(current_module: str, node: ast.ImportFrom, is_pkg_init: bool) -> list[str]:
    """Resolve relative ImportFrom to an absolute module name (one candidate)."""
    if node.level == 0:
        return []
    parts = current_module.split(".")
    if is_pkg_init:
        anchor_parts = parts
    else:
        anchor_parts = parts[:-1] if len(parts) > 1 else parts
    up = node.level - 1
    if len(anchor_parts) < up:
        return []
    base_parts = anchor_parts[:-up] if up else anchor_parts
    if node.module:
        return [".".join([*base_parts, *node.module.split(".")])]
    return [".".join(base_parts)] if base_parts else []


def extract_ddtrace_imports(
    source: str,
    current_module: str,
    is_pkg_init: bool,
    collect_aliases: bool,
    extra_components: frozenset[str],
) -> set[str]:
    out: set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "ddtrace" or alias.name.startswith("ddtrace."):
                    out.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                if node.module == "ddtrace" or node.module.startswith("ddtrace."):
                    out.add(node.module)
                    for alias in node.names:
                        path = f"{node.module}.{alias.name}"
                        if path in extra_components or node.module in extra_components or collect_aliases:
                            out.add(path)
            elif node.level > 0:
                for base in resolve_relative(current_module, node, is_pkg_init):
                    if base == "ddtrace" or base.startswith("ddtrace."):
                        out.add(base)
                        for alias in node.names:
                            path = f"{base}.{alias.name}"
                            if path in extra_components or base in extra_components or collect_aliases:
                                out.add(path)
    return out


def collect_edges(
    ddtrace_root: Path,
    detailed_components: tuple[str, ...],
    *,
    min_dependents_per_node: int,
    min_edge_weight: int,
    collect_aliases: bool,
    extra_components: tuple[str, ...] = (),
) -> tuple[set[tuple[str, str]], dict[tuple[str, str], int]]:
    """Return (directed edges between buckets, edge weights).

    Edge (src, dst) means src imports something from dst.
    Edge weights count import references. Edges below ``min_edge_weight`` are dropped.
    """
    edges: dict[tuple[str, str], int] = defaultdict(int)
    dependents: defaultdict[str, int] = defaultdict(int)
    extra_set = frozenset(extra_components)
    skip = {"vendor", "__pycache__", ".pytest_cache", "test-results"}
    for py in sorted(ddtrace_root.rglob("*.py")):
        rel_parts = py.relative_to(ddtrace_root).parts
        if rel_parts and rel_parts[0] in skip:
            continue
        if "__pycache__" in rel_parts:
            continue
        if any(p.startswith(".") for p in rel_parts):
            continue
        mod, is_pkg_init = path_to_module(ddtrace_root, py)
        bucket_src = path_bucket(py, ddtrace_root, detailed_components)
        try:
            text = py.read_text(encoding="utf-8")
        except OSError:
            continue
        for imp in extract_ddtrace_imports(text, mod, is_pkg_init, collect_aliases, extra_set):
            bucket_dst = import_target_bucket(imp, detailed_components, extra_components)
            if bucket_src is None or bucket_dst is None or bucket_src == bucket_dst:
                continue
            dependents[bucket_dst] += 1
            if dependents[bucket_dst] > min_dependents_per_node:
                edges[(bucket_src, bucket_dst)] += 1
    edges = {k: v for k, v in edges.items() if v >= min_edge_weight}
    return set(edges), dict(edges)


def filter_excluded_graph_nodes(
    edges: set[tuple[str, str]],
    weights: dict[tuple[str, str], int],
    excluded: frozenset[str],
) -> tuple[set[tuple[str, str]], dict[tuple[str, str], int]]:
    """Remove nodes by exact name; drop every edge whose source or target is excluded."""
    if not excluded:
        return edges, weights
    new_edges = {(a, b) for a, b in edges if a not in excluded and b not in excluded}
    return new_edges, {e: weights[e] for e in new_edges}


def _short_label(node: str) -> str:
    if node == "ddtrace (root)":
        return "(root)"
    if node.startswith("ddtrace."):
        return node[len("ddtrace.") :]
    return node
