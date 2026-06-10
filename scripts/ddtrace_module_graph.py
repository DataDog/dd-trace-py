#!/usr/bin/env scripts/uv-run-script
# -*- mode: python -*-
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "matplotlib",
#   "networkx",
# ]
# ///

"""Build a coarse inter-subsystem dependency graph for the ddtrace package.

Each **source** node is determined by the file path: the first directory under
``ddtrace/`` (for example ``internal/``, ``_trace/``), or ``ddtrace (root)`` for
files directly under ``ddtrace/``. **Import targets** use the same grouping:
imports of top-level modules like ``ddtrace.constants`` map to ``(root)``, while
``ddtrace.internal.x`` maps to ``ddtrace.internal``. Files under ``ddtrace/vendor/``
and other dot-prefixed paths are skipped.

Run with ``uv run --script scripts/ddtrace_module_graph.py`` (or execute this file
if ``scripts/uv-run-script`` is on ``PATH``).
"""

from __future__ import annotations

import argparse
import ast
from collections import defaultdict
import html
import json
from pathlib import Path
import sys
from typing import Optional


COMPONENT_LAYERS = [
    ["ddtrace.internal"],
    ["ddtrace._trace"],
    [
        "ddtrace.testing",
        "ddtrace.appsec",
        "ddtrace.trace",
        "ddtrace.llmobs",
        "ddtrace.profiling",
        "ddtrace.debugging",
        "ddtrace.errortracking",
    ],
    ["ddtrace.propagation"],
    [
        "ddtrace.commands",
        "ddtrace.bootstrap",
        "ddtrace.vendor",
        "ddtrace.contrib",
        "ddtrace.ext",
        "ddtrace.opentelemetry",
        "ddtrace.openfeature",
        "ddtrace.runtime",
        "ddtrace.sourcecode",
    ],
]
COMPONENT_TO_LAYER = {s: i for i, layer in enumerate(COMPONENT_LAYERS) for s in layer}


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
        return
    top = parts[0]
    if not top or top in detailed_components or top.startswith("."):
        return
    for component in detailed_components:
        component_parts = component.split(".")
        comparison = list(zip(component_parts, parts))
        if all(c == p for c, p in comparison):
            return f"ddtrace.{component}.{parts[len(component_parts)]}"
    return f"ddtrace.{top}"


def import_target_bucket(mod: str, detailed_components: tuple[str, ...]) -> Optional[str]:
    """Bucket import targets the same way as ``path_bucket`` (root modules collapse to ``(root)``)."""
    if mod == "ddtrace":
        return
    if not mod.startswith("ddtrace."):
        return mod
    rest = mod[len("ddtrace.") :]
    if not rest:
        return
    first, _, tail = rest.partition(".")
    parts = rest.split(".")
    for component in detailed_components:
        component_parts = component.split(".")
        comparison = list(zip(component_parts, parts))
        if all(c == p for c, p in comparison):
            return f"ddtrace.{component}.{'.'.join(parts[len(component_parts) :])}"
    if not first or first.startswith(".") or any(a.startswith(first) for a in detailed_components) or not tail:
        return
    return f"ddtrace.{first}"


def resolve_relative(current_module: str, node: ast.ImportFrom, is_pkg_init: bool) -> list[str]:
    """Resolve relative ImportFrom to an absolute module name (one candidate)."""
    if node.level == 0:
        return []
    parts = current_module.split(".")
    # Package that contains this module (PEP 328 / __package__ semantics, simplified).
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


def extract_ddtrace_imports(source: str, current_module: str, is_pkg_init: bool) -> set[str]:
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
            elif node.level > 0:
                for base in resolve_relative(current_module, node, is_pkg_init):
                    if base == "ddtrace" or base.startswith("ddtrace."):
                        out.add(base)
    return out


def collect_edges(
    ddtrace_root: Path, detailed_components: tuple[str, ...]
) -> tuple[set[tuple[str, str]], dict[str, int]]:
    """Return (directed edges between buckets, edge weights)."""
    edges: defaultdict[tuple[str, str], int] = defaultdict(int)
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
        for imp in extract_ddtrace_imports(text, mod, is_pkg_init):
            bucket_dst = import_target_bucket(imp, detailed_components)
            if None in (bucket_src, bucket_dst) or bucket_src == bucket_dst:
                continue
            edges[(bucket_src, bucket_dst)] += 1
    return set(edges), dict(edges)


def _short_label(node: str) -> str:
    if node == "ddtrace (root)":
        return "(root)"
    if node.startswith("ddtrace."):
        return node[len("ddtrace.") :]
    return node


def render_graph(edges: set[tuple[str, str]], weights: dict[tuple[str, str], int], out_png: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import networkx as nx
    import numpy as np

    g = nx.DiGraph()
    for a, b in edges:
        g.add_edge(a, b, weight=weights.get((a, b), 1))

    for node in g.nodes:
        for layer, components in enumerate(COMPONENT_LAYERS):
            for component in components:
                if node.startswith(component):
                    g.nodes[node]["subset"] = layer
                    break
            if "subset" in g.nodes[node] and g.nodes[node]["subset"] == layer:
                break

    pos = nx.multipartite_layout(g, align="vertical", scale=2)
    # Widen horizontal gaps between subset layers (x is the layer axis for align="vertical").
    LAYER_GAP_SCALE = 8.0
    if pos:
        axis = 0
        center_axis = float(np.mean([pos[n][axis] for n in pos]))
        for n in pos:
            p = np.asarray(pos[n], dtype=float)
            p[axis] = center_axis + LAYER_GAP_SCALE * (p[axis] - center_axis)
            pos[n] = p

    labels = {n: _short_label(n) for n in g.nodes}

    plt.figure(figsize=(200, 200), dpi=130)
    nx.draw_networkx_nodes(g, pos, node_color="#1f77b4", node_size=4200, alpha=0.92)
    nx.draw_networkx_labels(g, pos, labels, font_size=21, font_color="black", font_weight="bold")
    nx.draw_networkx_edges(
        g,
        pos,
        edge_color="#333333",
        arrows=True,
        arrowsize=22,
        arrowstyle="->",
        min_source_margin=30,
        min_target_margin=30,
        width=[min(3.5, 0.35 + weights.get(e, 1) ** 0.35) for e in g.edges],
        alpha=0.45,
        connectionstyle="arc3,rad=0.2",
    )
    plt.axis("off")
    plt.title(
        "ddtrace inter-subsystem imports\n"
        "(each node = first path segment under ddtrace/, e.g. internal, _trace, contrib; "
        "arrows = static import references aggregated across .py files; vendor/ not scanned)",
        fontsize=13,
        pad=18,
    )
    out_png.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_png, bbox_inches="tight", facecolor="white")
    plt.close()


def render_html(
    edges: set[tuple[str, str]],
    weights: dict[tuple[str, str], int],
    out_html: Path,
) -> None:
    """Write a self-contained interactive page (vis-network from CDN; drag nodes, pan/zoom)."""
    all_nodes: set[str] = set()
    for a, b in edges:
        all_nodes.add(a)
        all_nodes.add(b)
    nodes_list = [{"id": n, "label": _short_label(n), "title": html.escape(n)} for n in sorted(all_nodes)]
    edges_list = [
        {
            "from": a,
            "to": b,
            "value": weights[(a, b)],
            "title": f"{weights[(a, b)]} static import references",
        }
        for a, b in sorted(edges)
    ]
    graph_json = json.dumps({"nodes": nodes_list, "edges": edges_list}, separators=(",", ":"))

    # vis-network UMD + CSS from CDN; physics runs once then off so drag positions persist.
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>ddtrace module dependencies</title>
  <link rel="stylesheet" href="https://unpkg.com/vis-network@9.1.9/styles/vis-network.min.css" />
  <style>
    html, body {{ height: 100%; margin: 0; font-family: system-ui, sans-serif; }}
    #meta {{
      padding: 0.6rem 1rem;
      border-bottom: 1px solid #ccc;
      background: #f6f8fa;
      font-size: 0.9rem;
    }}
    #meta h1 {{ margin: 0 0 0.35rem 0; font-size: 1.1rem; }}
    #meta p {{ margin: 0; color: #444; max-width: 52rem; }}
    #graph {{ width: 100%; height: calc(100% - 5.5rem); border: none; }}
  </style>
</head>
<body>
  <div id="meta">
    <h1>ddtrace inter-subsystem imports</h1>
    <p>
      Nodes group files by first directory under <code>ddtrace/</code> (or <code>(root)</code> for
      top-level modules). Arrows aggregate static <code>import</code> / <code>from</code> references.
      <strong>Drag</strong> nodes to rearrange; drag background to pan; scroll to zoom.
      Regenerate with <code>uv run --script scripts/ddtrace_module_graph.py</code>.
    </p>
  </div>
  <div id="graph"></div>
  <script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
  <script type="application/json" id="graph-data">{graph_json}</script>
  <script>
    const payload = JSON.parse(document.getElementById("graph-data").textContent);
    const nodes = new vis.DataSet(payload.nodes);
    const edgs = new vis.DataSet(
      payload.edges.map((e) => ({{ ...e, arrows: "from,to,middle" }}))
    );
    const container = document.getElementById("graph");
    const data = {{ nodes, edges: edgs }};
    const options = {{
      nodes: {{
        shape: "box",
        margin: 12,
        font: {{ size: 45, color: "#ffffff", face: "system-ui, sans-serif" }},
        color: {{ background: "#1f77b4", border: "#145a8a", highlight: {{ background: "#42a5f5", border: "#0d47a1" }} }},
        borderWidth: 2,
        shadow: true,
      }},
      edges: {{
        color: {{ color: "#555", highlight: "#1976d2", hover: "#333" }},
        smooth: {{ type: "vertical" }},
        scaling: {{ min: 5, max: 30, label: {{ enabled: true }} }},
      }},
      physics: {{
        enabled: true,
        stabilization: {{ iterations: 220, updateInterval: 5 }},
        barnesHut: {{
          gravitationalConstant: -50500,
          centralGravity: 1,
          springLength: 5000,
          springConstant: 0.532,
        }},
      }},
      interaction: {{
        dragNodes: true,
        dragView: true,
        zoomView: true,
        hover: true,
        tooltipDelay: 120,
      }},
    }};
    const network = new vis.Network(container, data, options);
    network.once("stabilizationIterationsDone", function () {{
      network.setOptions({{ physics: false }});
    }});
  </script>
</body>
</html>
"""
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(page, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--detailed-component",
        dest="detailed_components",
        action="append",
        metavar="PREFIX",
        help=(
            "Path prefix under ddtrace/ (no leading ``ddtrace/``), e.g. ``internal`` or "
            "``contrib.internal``, for finer-grained buckets. Repeat for multiple. "
            "If this option is never passed, defaults to ``internal`` and ``contrib.internal``. "
            "If passed at least once, only the given prefixes are used."
        ),
    )
    parser.add_argument(
        "--png",
        action="store_true",
        help="Also write docs/images/ddtrace-module-dependencies.png (requires matplotlib, networkx).",
    )
    args = parser.parse_args()

    detailed_components: tuple[str, ...] = (
        tuple(args.detailed_components) if args.detailed_components is not None else tuple()
    )

    repo = Path(__file__).resolve().parents[1]
    ddtrace_root = repo / "ddtrace"
    if not ddtrace_root.is_dir():
        print("ddtrace package not found", file=sys.stderr)
        return 1
    edges, weights = collect_edges(ddtrace_root, detailed_components)
    out_html = repo / "docs" / "ddtrace-module-dependencies.html"
    render_html(edges, weights, out_html)
    print(f"Wrote {out_html} ({len(edges)} edges)")
    if args.png:
        out_png = repo / "docs" / "images" / "ddtrace-module-dependencies.png"
        render_graph(edges, weights, out_png)
        print(f"Wrote {out_png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
