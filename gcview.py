"""Convert gc_graph.json into a self-contained interactive HTML viewer.

Usage:
    python gcview.py [gc_graph.json] [output.html]

The viewer has two modes:
  - Type summary  : each node = a type, size ∝ instance count,
                    edge weight = cross-type reference count.
  - Object detail : click a type node to see its actual objects and
                    their immediate neighbours (capped at MAX_DETAIL_NODES).
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from typing import Any


MAX_DETAIL_NODES = 300  # max objects shown when drilling into a type


def build_type_summary(graph: dict[str, Any]) -> dict[str, Any]:
    type_registry: dict[str, str] = graph["type_registry"]
    nodes_raw: dict[str, dict[str, Any]] = graph["nodes"]
    edges_raw: list[list[str]] = graph["edges"]

    # count instances per type
    type_count: dict[str, int] = defaultdict(int)
    obj_to_type: dict[str, str] = {}
    for oid, node in nodes_raw.items():
        tid = str(node["type_id"])
        type_count[tid] += 1
        obj_to_type[oid] = tid

    # count cross-type edges
    edge_weights: dict[tuple[str, str], int] = defaultdict(int)
    for src, dst in edges_raw:
        st = obj_to_type.get(src)
        dt = obj_to_type.get(dst)
        if st and dt and st != dt:
            key = (min(st, dt), max(st, dt))
            edge_weights[key] += 1

    summary_nodes = [
        {
            "id": tid,
            "name": type_registry.get(tid, tid),
            "count": type_count[tid],
        }
        for tid in type_count
    ]
    summary_edges = [
        {"source": s, "target": d, "weight": w}
        for (s, d), w in edge_weights.items()
    ]

    return {"nodes": summary_nodes, "edges": summary_edges}


def build_detail_index(graph: dict[str, Any]) -> dict[str, Any]:
    """For each type_id, keep up to MAX_DETAIL_NODES object ids."""
    nodes_raw: dict[str, dict[str, Any]] = graph["nodes"]
    edges_raw: list[list[str]] = graph["edges"]
    type_registry: dict[str, str] = graph["type_registry"]

    # group objects by type
    by_type: dict[str, list[str]] = defaultdict(list)
    for oid, node in nodes_raw.items():
        by_type[str(node["type_id"])].append(oid)

    # adjacency set for quick neighbour lookup
    adj: dict[str, set[str]] = defaultdict(set)
    for src, dst in edges_raw:
        adj[src].add(dst)
        adj[dst].add(src)

    detail: dict[str, Any] = {}
    for tid, oids in by_type.items():
        sampled = oids[:MAX_DETAIL_NODES]
        sampled_set = set(sampled)

        # include immediate neighbours (up to cap)
        neighbour_ids: set[str] = set()
        for oid in sampled:
            for nb in adj[oid]:
                if nb not in sampled_set:
                    neighbour_ids.add(nb)
                    if len(neighbour_ids) + len(sampled) >= MAX_DETAIL_NODES:
                        break
            if len(neighbour_ids) + len(sampled) >= MAX_DETAIL_NODES:
                break

        all_ids = list(sampled_set | neighbour_ids)

        detail_nodes = []
        for oid in all_ids:
            n = nodes_raw[oid]
            detail_nodes.append(
                {
                    "id": oid,
                    "type_id": str(n["type_id"]),
                    "type_name": type_registry.get(str(n["type_id"]), "?"),
                    "generation": n.get("generation"),
                    "repr": n["repr"],
                    "is_focus": oid in sampled_set,
                }
            )

        detail_edges = []
        for src, dst in edges_raw:
            if src in sampled_set or dst in sampled_set:
                if src in set(all_ids) and dst in set(all_ids):
                    detail_edges.append({"source": src, "target": dst})

        detail[tid] = {"nodes": detail_nodes, "edges": detail_edges}

    return detail


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>GC Object Graph</title>
<script src="https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: #0f1117; color: #e0e0e0; display: flex; height: 100vh; overflow: hidden; }

  #sidebar {
    width: 280px; min-width: 220px; background: #181c25; border-right: 1px solid #2a2f3d;
    display: flex; flex-direction: column; overflow: hidden;
  }
  #sidebar h2 { padding: 14px 16px 8px; font-size: 13px; color: #8899bb; text-transform: uppercase; letter-spacing: .08em; }
  #meta { padding: 0 16px 12px; font-size: 11px; color: #99aac0; line-height: 1.7; border-bottom: 1px solid #2a2f3d; }
  #meta b { color: #c5d5ea; }
  #search { margin: 10px 12px 4px; padding: 6px 10px; background: #22273a; border: 1px solid #333a50; border-radius: 6px; color: #e0e0e0; font-size: 12px; width: calc(100% - 24px); }
  #search::placeholder { color: #556; }
  #type-list { overflow-y: auto; flex: 1; padding: 4px 0; }
  .type-item { padding: 5px 14px 5px 16px; font-size: 11px; cursor: pointer; display: flex; justify-content: space-between; align-items: center; border-left: 3px solid transparent; }
  .type-item:hover { background: #22283a; }
  .type-item.active { background: #1e2d4a; border-left-color: #4a90d9; }
  .type-badge { background: #2a3350; border-radius: 10px; padding: 1px 7px; font-size: 10px; color: #8899bb; }

  #main { flex: 1; position: relative; overflow: hidden; }
  #canvas { width: 100%; height: 100%; }

  #tooltip {
    position: absolute; pointer-events: none; background: #1a1f2e; border: 1px solid #3a4560;
    border-radius: 8px; padding: 8px 12px; font-size: 11px; max-width: 320px; line-height: 1.6;
    box-shadow: 0 4px 20px rgba(0,0,0,.6); display: none;
  }
  #tooltip .tt-type { color: #7ab3f0; font-weight: 600; margin-bottom: 2px; }
  #tooltip .tt-repr { color: #c0d0e8; font-family: monospace; font-size: 10px; word-break: break-all; }
  #tooltip .tt-meta { color: #778899; margin-top: 4px; }

  #mode-bar {
    position: absolute; top: 12px; left: 50%; transform: translateX(-50%);
    background: #181c25cc; border: 1px solid #2a2f3d; border-radius: 20px;
    padding: 4px 8px; display: flex; gap: 4px; backdrop-filter: blur(6px);
  }
  .mode-btn {
    padding: 4px 14px; border-radius: 14px; border: none; cursor: pointer;
    font-size: 11px; font-weight: 600; background: transparent; color: #778;
  }
  .mode-btn.active { background: #2d4a7a; color: #afd0ff; }

  #back-btn {
    position: absolute; top: 12px; left: 14px; display: none;
    background: #2a3555; border: 1px solid #3a4f7a; border-radius: 16px;
    padding: 5px 14px; font-size: 11px; color: #aac8f0; cursor: pointer;
  }
  #back-btn:hover { background: #3a4a6a; }

  #detail-title {
    position: absolute; top: 14px; left: 50%; transform: translateX(-50%);
    font-size: 12px; color: #9ab8e0; pointer-events: none; white-space: nowrap;
  }
</style>
</head>
<body>

<div id="sidebar">
  <h2>GC Graph Viewer</h2>
  <div id="meta"></div>
  <input id="search" type="text" placeholder="Filter types…"/>
  <div id="type-list"></div>
</div>

<div id="main">
  <svg id="canvas"></svg>
  <div id="tooltip"></div>
  <button id="back-btn">← back to summary</button>
  <div id="detail-title"></div>
</div>

<script>
const META    = __META__;
const SUMMARY = __SUMMARY__;
const DETAIL  = __DETAIL__;

// ── meta panel ──────────────────────────────────────────────────────────────
const metaEl = document.getElementById("meta");
metaEl.innerHTML = `
  <b>${META.n_nodes.toLocaleString()}</b> objects &nbsp;·&nbsp;
  <b>${META.n_edges.toLocaleString()}</b> refs &nbsp;·&nbsp;
  <b>${META.n_types}</b> types<br>
  GC tracked: <b>${META.n_gc_tracked.toLocaleString()}</b>
  &nbsp;(gen0 ${META.n_gc_tracked_by_gen[0]} / gen1 ${META.n_gc_tracked_by_gen[1]} / gen2 ${META.n_gc_tracked_by_gen[2]})<br>
  Build: <b>${META.total_ms} ms</b>
  &nbsp;(collect ${META.gc_collect_ms} ms / traverse ${META.traverse_ms} ms)
`;

// ── type list ────────────────────────────────────────────────────────────────
const typeListEl = document.getElementById("type-list");
const searchEl   = document.getElementById("search");

// sorted by instance count desc
const sortedTypes = [...SUMMARY.nodes].sort((a,b) => b.count - a.count);

function renderTypeList(filter) {
  typeListEl.innerHTML = "";
  const q = filter.toLowerCase();
  sortedTypes.forEach(t => {
    if (q && !t.name.toLowerCase().includes(q)) return;
    const div = document.createElement("div");
    div.className = "type-item";
    div.dataset.tid = t.id;
    div.innerHTML = `<span title="${t.name}">${shortName(t.name)}</span>
                     <span class="type-badge">${t.count}</span>`;
    div.addEventListener("click", () => showDetail(t.id, t.name));
    typeListEl.appendChild(div);
  });
}
searchEl.addEventListener("input", () => renderTypeList(searchEl.value));
renderTypeList("");

function shortName(s) {
  const parts = s.split(".");
  return parts.length > 2 ? "…" + parts.slice(-2).join(".") : s;
}

// ── D3 setup ─────────────────────────────────────────────────────────────────
const svg    = d3.select("#canvas");
const width  = () => svg.node().clientWidth;
const height = () => svg.node().clientHeight;

const g      = svg.append("g");
const tooltip = document.getElementById("tooltip");
const backBtn = document.getElementById("back-btn");
const detailTitle = document.getElementById("detail-title");

svg.call(d3.zoom().scaleExtent([0.05, 8]).on("zoom", e => g.attr("transform", e.transform)));

// colour scale for types (in detail view)
const typeColour = d3.scaleOrdinal(d3.schemeTableau10.concat(d3.schemePastel1));

let simulation = null;

function clearGraph() {
  if (simulation) simulation.stop();
  g.selectAll("*").remove();
}

// ── summary view ──────────────────────────────────────────────────────────────
function showSummary() {
  clearGraph();
  backBtn.style.display = "none";
  detailTitle.textContent = "";
  document.querySelectorAll(".type-item").forEach(el => el.classList.remove("active"));

  const maxCount = d3.max(SUMMARY.nodes, d => d.count) || 1;
  const r = d => 4 + 28 * Math.sqrt(d.count / maxCount);
  const maxW = d3.max(SUMMARY.edges, d => d.weight) || 1;
  const lw = d => 0.3 + 3 * (d.weight / maxW);

  const nodes = SUMMARY.nodes.map(d => ({...d}));
  const edges = SUMMARY.edges.map(d => ({...d}));
  const nodeById = Object.fromEntries(nodes.map(n => [n.id, n]));

  // resolve edge references
  edges.forEach(e => { e.source = nodeById[e.source]; e.target = nodeById[e.target]; });

  const link = g.append("g").attr("stroke", "#2a3555").selectAll("line")
    .data(edges).join("line")
    .attr("stroke-width", lw)
    .attr("stroke-opacity", 0.6);

  const node = g.append("g").selectAll("circle")
    .data(nodes).join("circle")
    .attr("r", r)
    .attr("fill", d => typeColour(d.id))
    .attr("fill-opacity", 0.85)
    .attr("stroke", "#000")
    .attr("stroke-width", 0.5)
    .style("cursor", "pointer")
    .on("mouseover", (event, d) => showTip(event, `<div class="tt-type">${d.name}</div><div class="tt-meta">${d.count.toLocaleString()} instances</div>`))
    .on("mousemove", moveTip)
    .on("mouseout", hideTip)
    .on("click", (event, d) => { event.stopPropagation(); showDetail(d.id, d.name); })
    .call(d3.drag()
      .on("start", (event, d) => { if (!event.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
      .on("drag",  (event, d) => { d.fx = event.x; d.fy = event.y; })
      .on("end",   (event, d) => { if (!event.active) simulation.alphaTarget(0); d.fx = null; d.fy = null; }));

  simulation = d3.forceSimulation(nodes)
    .force("link",   d3.forceLink(edges).distance(80).strength(0.4))
    .force("charge", d3.forceManyBody().strength(-120))
    .force("center", d3.forceCenter(width() / 2, height() / 2))
    .force("collide", d3.forceCollide().radius(d => r(d) + 2))
    .on("tick", () => {
      link.attr("x1", d => d.source.x).attr("y1", d => d.source.y)
          .attr("x2", d => d.target.x).attr("y2", d => d.target.y);
      node.attr("cx", d => d.x).attr("cy", d => d.y);
    });
}

// ── detail view ───────────────────────────────────────────────────────────────
function showDetail(tid, typeName) {
  const data = DETAIL[tid];
  if (!data) return;
  clearGraph();

  document.querySelectorAll(".type-item").forEach(el =>
    el.classList.toggle("active", el.dataset.tid === tid));

  backBtn.style.display = "block";
  detailTitle.textContent = typeName + ` (${data.nodes.filter(n=>n.is_focus).length} objects shown)`;

  const GEN_COLOUR = ["#4a9eff", "#f0a040", "#e05050", "#888"];
  const nodes = data.nodes.map(d => ({...d}));
  const nodeById = Object.fromEntries(nodes.map(n => [n.id, n]));
  const edges = data.edges
    .map(d => ({source: nodeById[d.source], target: nodeById[d.target]}))
    .filter(e => e.source && e.target);

  const link = g.append("g").attr("stroke", "#2a3a55").selectAll("line")
    .data(edges).join("line")
    .attr("stroke-width", 1)
    .attr("stroke-opacity", 0.5);

  // arrow marker
  svg.append("defs").append("marker")
    .attr("id","arr").attr("viewBox","0 -4 8 8").attr("refX",14).attr("refY",0)
    .attr("markerWidth",6).attr("markerHeight",6).attr("orient","auto")
    .append("path").attr("d","M0,-4L8,0L0,4").attr("fill","#3a5070");
  link.attr("marker-end","url(#arr)");

  const node = g.append("g").selectAll("circle")
    .data(nodes).join("circle")
    .attr("r", d => d.is_focus ? 8 : 5)
    .attr("fill", d => d.is_focus ? typeColour(d.type_id) : "#445566")
    .attr("fill-opacity", d => d.is_focus ? 0.9 : 0.6)
    .attr("stroke", d => d.is_focus ? "#ffffff44" : "none")
    .attr("stroke-width", 1.5)
    .style("cursor", "pointer")
    .on("mouseover", (event, d) => showTip(event,
      `<div class="tt-type">${d.type_name}</div>
       <div class="tt-repr">${escHtml(d.repr)}</div>
       <div class="tt-meta">gen ${d.generation ?? "?"} &nbsp;·&nbsp; id ${d.id}</div>`))
    .on("mousemove", moveTip)
    .on("mouseout", hideTip)
    .call(d3.drag()
      .on("start", (event, d) => { if (!event.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
      .on("drag",  (event, d) => { d.fx = event.x; d.fy = event.y; })
      .on("end",   (event, d) => { if (!event.active) simulation.alphaTarget(0); d.fx = null; d.fy = null; }));

  simulation = d3.forceSimulation(nodes)
    .force("link",   d3.forceLink(edges).distance(60).strength(0.5))
    .force("charge", d3.forceManyBody().strength(-80))
    .force("center", d3.forceCenter(width() / 2, height() / 2))
    .force("collide", d3.forceCollide().radius(10))
    .on("tick", () => {
      link.attr("x1", d => d.source.x).attr("y1", d => d.source.y)
          .attr("x2", d => d.target.x).attr("y2", d => d.target.y);
      node.attr("cx", d => d.x).attr("cy", d => d.y);
    });
}

backBtn.addEventListener("click", showSummary);

// ── tooltip helpers ───────────────────────────────────────────────────────────
function showTip(event, html) { tooltip.style.display = "block"; tooltip.innerHTML = html; moveTip(event); }
function moveTip(event) {
  let x = event.clientX + 14, y = event.clientY + 14;
  if (x + 340 > window.innerWidth)  x = event.clientX - 340;
  if (y + 120 > window.innerHeight) y = event.clientY - 80;
  tooltip.style.left = x + "px"; tooltip.style.top = y + "px";
}
function hideTip() { tooltip.style.display = "none"; }
function escHtml(s) { return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }

showSummary();
</script>
</body>
</html>
"""


def generate_html(graph: dict[str, Any]) -> str:
    summary = build_type_summary(graph)
    detail = build_detail_index(graph)
    meta = graph["meta"]

    html = HTML_TEMPLATE
    html = html.replace("__META__",    json.dumps(meta))
    html = html.replace("__SUMMARY__", json.dumps(summary))
    html = html.replace("__DETAIL__",  json.dumps(detail))
    return html


def main() -> None:
    input_path  = sys.argv[1] if len(sys.argv) > 1 else "gc_graph.json"
    output_path = sys.argv[2] if len(sys.argv) > 2 else "gc_graph.html"

    print(f"reading {input_path} …")
    with open(input_path) as f:
        graph = json.load(f)

    print("building summary + detail index …")
    html = generate_html(graph)

    with open(output_path, "w") as f:
        f.write(html)
    print(f"written to {output_path}")


if __name__ == "__main__":
    main()
