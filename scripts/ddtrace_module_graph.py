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
import html
import json
import math
from pathlib import Path
import sys

from _import_graph import _short_label
from _import_graph import collect_edges
from _import_graph import filter_excluded_graph_nodes


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


def _normalize_detailed_component_prefix(prefix: str) -> str:
    """Map CLI ``--detailed-component`` to the path-under-``ddtrace/`` form used internally."""
    p = prefix.strip()
    if p.startswith("ddtrace."):
        return p[len("ddtrace.") :]
    return p


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
      payload.edges.map((e) => ({{ ...e, arrows: "to,middle" }}))
    );
    const container = document.getElementById("graph");
    const data = {{ nodes, edges: edgs }};
    const options = {{
      nodes: {{
        shape: "box",
        margin: 12,
        font: {{ size: {70 if len(nodes_list) > 300 else 50}, color: "#ffffff", face: "system-ui, sans-serif" }},
        color: {{ background: "#1f77b4", border: "#145a8a",
                  highlight: {{ background: "#42a5f5", border: "#0d47a1" }} }},
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
          gravitationalConstant: {-300 * len(nodes_list)},
          centralGravity: 1,
          springLength: {250 * math.sqrt(len(nodes_list) / 4)},
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
            "Path prefix under ``ddtrace/`` (e.g. ``internal`` or ``contrib.internal``), or the same "
            "path with a ``ddtrace.`` module prefix (e.g. ``ddtrace.internal``), for finer-grained buckets. "
            "Repeat for multiple. If this option is never passed, defaults to ``internal`` and "
            "``contrib.internal``. If passed at least once, only the given prefixes are used."
        ),
    )
    parser.add_argument(
        "--min-dependents-per-node",
        type=int,
        default=0,
        metavar="N",
        help=("Only count an edge after its destination bucket has accumulated more than N events (default: 0)."),
    )
    parser.add_argument(
        "--min-edge-weight",
        type=int,
        default=0,
        metavar="W",
        help=(
            "After aggregation, drop directed edges whose import-count weight is strictly less than W "
            "(default: 0, i.e. keep all non-empty edges)."
        ),
    )
    parser.add_argument(
        "--extra-component",
        dest="extra_components",
        action="append",
        metavar="MODULE",
        help=(
            "Full ``ddtrace...`` module path to treat as its own graph bucket and to expand in "
            "``from ddtrace... import ...`` (e.g. ``ddtrace.internal.span``). Repeat for multiple."
        ),
    )
    parser.add_argument(
        "--exclude-component",
        dest="exclude_components",
        action="append",
        metavar="NODE",
        help=(
            "Exact graph node id to remove (e.g. ``ddtrace.contrib`` or ``ddtrace (root)``). "
            "Drops all edges whose source or target is one of these names. Repeat for multiple."
        ),
    )
    parser.add_argument(
        "--collect-import-aliases",
        dest="collect_aliases",
        action="store_true",
        help=(
            "For ``from ddtrace... import ...``, record each imported symbol as its full submodule path "
            "(e.g. ``ddtrace.internal.span.Span``) instead of only the package prefix (e.g. ``ddtrace.internal``)."
        ),
    )
    parser.add_argument(
        "--png",
        action="store_true",
        help="Also write docs/images/ddtrace-module-dependencies.png (requires matplotlib, networkx).",
    )
    args = parser.parse_args()

    detailed_components: tuple[str, ...] = (
        tuple(_normalize_detailed_component_prefix(p) for p in args.detailed_components)
        if args.detailed_components is not None
        else tuple()
    )
    extra_components: tuple[str, ...] = tuple(args.extra_components or ())
    exclude_nodes = frozenset(s.strip() for s in (args.exclude_components or ()) if s.strip())

    repo = Path(__file__).resolve().parents[1]
    ddtrace_root = repo / "ddtrace"
    if not ddtrace_root.is_dir():
        print("ddtrace package not found", file=sys.stderr)
        return 1
    edges, weights = collect_edges(
        ddtrace_root,
        detailed_components,
        min_dependents_per_node=args.min_dependents_per_node,
        min_edge_weight=args.min_edge_weight,
        collect_aliases=args.collect_aliases,
        extra_components=extra_components,
    )
    edges, weights = filter_excluded_graph_nodes(edges, weights, exclude_nodes)
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
